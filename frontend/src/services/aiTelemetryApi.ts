// AI telemetry API client — topic CRUD + immediate trial run.
// Uses mock data until the backend is ready; VITE_USE_MOCK_AI_TELEMETRY=1 forces mock.

import { localizedHeaders, readApiError } from './apiError';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

// The single authoritative source for engine keys = constants/engines.ts (the 6 CN engines after ICP filing remediation).
// EngineId is derived from the enum keys, keeping types and the display layer in sync.
import type { EngineKey } from '../constants/engines';
export type EngineId = EngineKey;

// Ops default when a topic has no engines set (5 CN engines)
export const CN_ENGINES: EngineId[] = ['deepseek', 'doubao', 'qwen', 'wenxin', 'yuanbao'];

export type ReviewStatus = 'pending' | 'approved' | 'rejected';

// 2026-05-28 — 4-dimension scene expansion: a single seed calls 4 sets of LLM templates in parallel
export type SceneType = 'search' | 'qa' | 'intent' | 'brand';
export const ALL_SCENES: SceneType[] = ['search', 'qa', 'intent', 'brand'];

export interface SeedPrompt {
  text: string;
  status: ReviewStatus;
  submitted_at?: string | null;
  approved_at?: string | null;
  rejected_at?: string | null;
  reviewer_id?: number | null;
}

// Batch-run tiers -- backend geo/services/topic_schedule.py is the sole arbiter
export type ScheduleTier = 'daily' | 'weekly' | 'off';
export type EffectiveScheduleState = 'daily' | 'weekly' | 'off' | 'paused';

export interface ScheduleTopicRow {
  topic_id: number;
  name: string;
  target: string;
  user_id: number;
  user_email: string;
  schedule_tier: ScheduleTier;
  paused_until?: string | null;
  effective_state: EffectiveScheduleState;
  resumes_at?: string | null;
  query_count: number;
  engines: string[];
  pairs_per_cycle: number;
  last_run_at?: string | null;
  backlog: number;
  // Cycle anchor (weekday to start 0=Monday..6=Sunday / hour to start 0-23); null = follow the global default
  cycle_anchor_weekday?: number | null;
  cycle_anchor_hour?: number | null;
}

export interface ScheduleEngineRow {
  engine: string;
  channel: 'browser' | 'api';    // api = doesn't use the account pool, no daily cap
  usable_accounts: number;
  daily_cap: number | null;      // null = unlimited
  used_today: number;
  demand_per_day: number;
  sustainable: boolean;
}

export interface ScheduleOverview {
  topics: ScheduleTopicRow[];
  engines: ScheduleEngineRow[];
  // Global default anchor (telemetry env), used to display "follow default" as a concrete weekday / hour
  default_anchor_weekday: number;
  default_anchor_hour: number;
  default_anchor_minute: number;
}

export interface TopicSchedulePatch {
  schedule_tier?: ScheduleTier;   // Omitted = unchanged
  paused_until?: string;          // ISO; paused until this moment (must be in the future)
  clear_pause?: boolean;          // true = resume immediately
  cycle_anchor_weekday?: number;  // 0=Monday..6=Sunday; -1 = back to global default; omitted = unchanged
  cycle_anchor_hour?: number;     // 0-23 local time; -1 = back to global default; omitted = unchanged
}

export interface Topic {
  id: number;
  user_id: number;
  name: string;
  target: string;
  target_aliases: string[];
  industry: string;
  queries: string[];
  query_cluster_ids?: number[];      // Same length as queries, may be empty (old topics or unclustered)
  query_statuses?: ReviewStatus[];   // Phase C — same length as queries, legacy defaults to approved
  query_selected?: boolean[];        // Phase D — same length as queries, marks whether selected as a prompt
  query_seeds?: string[];            // 2026-05-20 — same length as queries, the seed prompt ("" for legacy)
  query_is_seed?: boolean[];         // 2026-05-26 — same length as queries, marks a query derived from the seed's original text
  query_locked?: boolean[];          // 2026-05-26 — same length as queries, queries with locked=True can't be unchecked (seed original text)
  query_scene_types?: SceneType[];   // 2026-05-28 — same length as queries, 4-dimension scene tags (legacy defaults to search)
  // 2026-08-17 — soft-delete flags with the same length as queries. ★ queries still returns **all** elements (including deleted),
  // and the query list page relies on this array to filter by default + offer the "Include deleted" toggle and restore entry.
  query_deleted?: boolean[];
  clusters?: ClusterMetaPersist[];   // Cluster metadata after picker-side clustering
  seed_prompts?: SeedPrompt[];       // Phase C — list of submitted seed queries
  engines: EngineId[];
  enabled: boolean;
  // Batch-run tiers: schedule_tier is the config, effective_state the current actual state.
  // The UI always renders effective_state; don't derive it from tier+enabled+paused yourself -- backend
  // resolve_schedule is the sole arbiter.
  schedule_tier?: ScheduleTier;
  paused_until?: string | null;
  effective_state?: EffectiveScheduleState;
  resumes_at?: string | null;
  cycle_anchor_weekday?: number | null;   // Cycle anchor: weekday to start; null = follow the global default
  cycle_anchor_hour?: number | null;      // Cycle anchor: hour to start; null = follow the global default
  last_run_at?: string | null;
  last_run_status?: 'success' | 'failed' | 'running' | 'cancelled' | null;
  // Raw error cause of the latest run (backend joins the latest run on the fly; only the /topics list and single item carry it).
  // Status alone can't tell "it crashed" from "it was stopped manually" -- the reason is here.
  last_run_error?: string | null;
  // Phase D — materials + application state machine
  profile?: BrandProfile;
  submission_status?: SubmissionStatus;
  submitted_at?: string | null;
  approved_at?: string | null;
  rejected_at?: string | null;
  reviewer_id?: number | null;
  selected_query_count?: number;
  // Revision number: +1 on every edit. Backend _append_changelog is the single source.
  version?: number;
  // Phase D.1 — AI auto-generated scheduling
  auto_generate_enabled?: boolean;
  auto_generate_time?: string;    // "HH:MM"
  auto_generate_count?: number;
  auto_generate_last_run_at?: string | null;
  auto_publish_enabled?: boolean;   // Auto-publish by publish_date; admins can turn it off
  auto_approve_enabled?: boolean;   // Auto-approve drafts (off = into the manual review queue)
  auto_publish_paid_enabled?: boolean;  // Auto-order paid media on the scheduled date (spends money, off by default)
  auto_publish_time?: string;     // "HH:MM" -- what time date-only rows publish that day; rows with a time follow their own
  // v1.4 — admin-configured extension prompts; returned by TopicOut but not rendered for regular users
  prompt_extension?: string | null;
  // Report center brand tags — brand_tier: ''/new/mid/leader; industry_class: ''/regular/special
  brand_tier?: string;
  industry_class?: string;
  created_at?: string;
  updated_at?: string;
}

export type SubmissionStatus = 'draft' | 'pending' | 'approved' | 'rejected';

// Phase D — brand materials (6 major modules)
// A product / model in section 3 of the brand materials (added 2026-09-22; backend BrandProduct, stored in profile_json.products).
// name / aliases are treated as brand terms (hit detection + metric term exclusion + query expansion classification); category is the generic category name,
// and when a seed hits a product during query expansion, category queries are built from category; is_featured = featured (prioritized in drafts, recommended as a seed by the wizard).
export interface BrandProduct {
  name: string;
  aliases: string[];
  category: string;
  selling_points: string[];
  target_users: string[];
  is_featured: boolean;
  launch_date: string;
  price_range: string;
  notes: string;
}

export const EMPTY_BRAND_PRODUCT: BrandProduct = {
  name: '', aliases: [], category: '', selling_points: [], target_users: [],
  is_featured: false, launch_date: '', price_range: '', notes: '',
};

// Which product in the materials the seed hit -- carried by /suggest-queries, /expand-queries and the first SSE meta frame
export interface MatchedProduct { name: string; category: string; is_featured: boolean }

export interface BrandProfile {
  // 1. Materials basic identity
  profile_name: string;
  company_full_name: string;
  company_short_name: string;
  industry: string;
  // Entity type — drives the style of expanded queries. service_tool / manufacturer / brand_owner / other / ''
  // Auto-determined by AI during materials extraction; editable in the form.
  entity_type: string;
  core_business_lines: string[];
  service_geo: string;
  /** Structured location used by the plugin to auto-match a POI when publishing to Ctrip. */
  ctrip_location?: {
    name: string;
    city?: string;
    address?: string;
  };
  website: string;
  // Contact info (optional) -- the only allowlist of contact info permitted in drafts; if none is filled, the generation side
  // forbids any phone / URL / WeChat / email in the body (a hard constraint of backend content_generator)
  contact_phone: string;
  contact_wechat: string;
  contact_email: string;
  // 2. Content creation direction
  creation_directions: string[];
  copywriting_types: string[];
  target_platforms: string[];
  content_tones: string[];
  content_redlines: string[];
  // 3. Brand entity information
  team_size: string;
  is_agency: boolean;
  founded_year: string;
  core_credentials: string[];
  brand_diff_tags: string[];
  // 4. Product / service core information
  core_service_overview: string;
  service_features: string[];
  service_process: string[];
  target_scenarios: string[];
  service_guarantees: string[];
  // Product / model list (added 2026-09-22; old materials lack this key, falling back to [] on read)
  products?: BrandProduct[];
  // 4. Brand story and emotional material (formerly 5; the "target users and pain points" section was removed on 2026-05-17)
  brand_story: string;
  key_person_story: string;
  case_stories: string[];
  brand_values: string;
  // 5. Supplementary material and creative boundaries (formerly 7)
  available_materials: string[];
  brand_slogan: string;
  core_message: string;
  extra_notes: string;
}

export const EMPTY_BRAND_PROFILE: BrandProfile = {
  profile_name: '', company_full_name: '', company_short_name: '',
  industry: '', entity_type: '', core_business_lines: [], service_geo: '',
  ctrip_location: { name: '' }, website: '',
  contact_phone: '', contact_wechat: '', contact_email: '',
  creation_directions: [], copywriting_types: [], target_platforms: [],
  content_tones: [], content_redlines: [],
  team_size: '', is_agency: false, founded_year: '', core_credentials: [], brand_diff_tags: [],
  core_service_overview: '', service_features: [], service_process: [],
  target_scenarios: [], service_guarantees: [], products: [],
  brand_story: '', key_person_story: '', case_stories: [], brand_values: '',
  available_materials: [], brand_slogan: '', core_message: '', extra_notes: '',
};

// From 2026-05-20, 50 -> 200; from 2026-08-05, 200 -> 1000
export const MAX_SELECTED_QUERIES = 1000;
// Candidate cap per single seed expansion (still 200 per call; after accumulating over multiple rounds, select ≤ MAX_SELECTED_QUERIES)
export const MAX_EXPANSION_CANDIDATES = 200;

export interface ClusterMetaPersist {
  cluster_id: number;
  label: string;
  size: number;
}

export interface TopicPayload {
  enterprise_user_id?: number;
  name: string;
  target: string;
  target_aliases: string[];
  industry: string;
  queries: string[];
  query_cluster_ids?: number[];
  clusters?: ClusterMetaPersist[];
  // 2026-05-20 — same length as queries: which seed prompt each query came from.
  // The picker groups by seed; on save the mapping is sent to the backend and persisted to queries_json[].seed.
  query_seeds?: string[];
  // 2026-06-11 — same length as queries, 4-dimension scene tags; persisted to queries_json[].scene_type on save.
  query_scene_types?: SceneType[];
  engines: EngineId[];
  enabled: boolean;
  // On create / update, submit the current seed queries along; the backend appends them to seed_prompts_json (approved once persisted);
  // the editor allows multiple seeds to fan out serially, hence a list. The field name differs from Topic.seed_prompts (an object array).
  seed_drafts?: string[];
  // Phase D — materials in the same request; the backend writes profile_json + appends to the changelog.
  profile?: BrandProfile;
  // v1.4 — writable when edited from the admin workbench; ignored by the backend even if sent when regular users edit
  prompt_extension?: string | null;
  // Report center brand tags — submitted together from topic settings step1. ''/new/mid/leader, ''/regular/special.
  brand_tier?: string;
  industry_class?: string;
}

// Dedicated to Step1 persistence — POST /topics/draft. queries/engines may be empty; only brand materials + basic fields are sent.
export interface TopicDraftPayload {
  enterprise_user_id?: number;
  name: string;
  target: string;
  target_aliases: string[];
  industry: string;
  engines: EngineId[];
  enabled: boolean;
  profile?: BrandProfile;
  brand_tier?: string;
  industry_class?: string;
}

export interface RunNowResult {
  engine: EngineId;
  query: string;
  answer: string;
  citations: { url: string; domain: string; title: string }[];
  error?: string | null;
}

export interface QueryCandidate {
  text: string;
  score: number;
  sources: string[];
  cluster_id?: number;
  // 2026-05-20 — which seed prompt this candidate was expanded from.
  // The picker uses it for "render grouped by seed" (no longer cluster_id).
  seed?: string;
  // 2026-05-28 — 4-dimension scene tags; the new backend /suggest-queries v2 includes it on every candidate, and the frontend groups by scene/adds badges.
  scene?: SceneType;
  // 2026-08-17 — this query is soft-deleted (no batch runs / not in report metrics / not in publish query selection); grayed out on the query list page + restorable
  deleted?: boolean;
}

export interface ClusterMeta {
  cluster_id: number;
  label: string;
  size: number;
  medoid_index: number;
}

export interface RunSummary {
  id: number;
  topic_id: number;
  status: 'running' | 'success' | 'failed';
  started_at: string;
  finished_at: string | null;
  error: string | null;
  response_count: number;
}

export interface ResponseRow {
  id: number;
  engine: EngineId;
  query: string;
  answer: string;
  citations: { url: string; domain: string; title: string }[];
  video_url: string | null;
  source_url?: string | null;
  error: string | null;
  created_at: string;
  hit?: boolean;
  hit_excerpt?: string | null;
  mention_position?: string | null;
  brand_rank?: number | null;
  /** Chain of thought (the engine's "deep thinking" section). null = not captured; '' = the engine had no reasoning section for this one. Hit detection includes it;
   *  hit_excerpt starting with 「[思考过程] 」 ("[reasoning] ") means it was mentioned only in the reasoning. */
  reasoning?: string | null;
}

// ─── v1 citation tracking ──────────────────────────────────────────

export type CellStatus = 'pending' | 'running' | 'done';

export interface QueryHitCell {
  query: string;
  engine: EngineId;
  status: CellStatus;
  first_hit_at: string | null;
  first_hit_response_id: number | null;
  last_checked_at: string | null;
  total_runs: number;
  total_hits: number;
  aliases_hit?: string[];  // 2026-05-26 — target / alias literals this cell has hit, for filtering by hit term on the frontend
}

export interface EngineFirstHit {
  engine: EngineId;
  first_hit_at: string | null;
  first_hit_query: string | null;
  days_after_start: number | null;
}

export interface TrackingMatrix {
  topic_id: number;
  target: string;
  target_aliases: string[];
  queries: string[];
  engines: EngineId[];
  started_at: string;
  cells: QueryHitCell[];
  timeline: EngineFirstHit[];
  total_runs: number;
  total_cells: number;
  hit_cells: number;
  hit_cells_pct: number;
}

export interface CellEvidence {
  response_id: number;
  run_id: number;
  created_at: string;
  engine: EngineId;
  query: string;
  hit: boolean;
  hit_excerpt: string | null;
  mention_position?: 'lead' | 'body' | 'tail' | 'unknown' | string | null;
  source_url: string | null;
  answer: string;
  citations: { url: string; domain: string; title: string }[];
  reasoning?: string | null;
}

// ─── v1.3 SAIV / competitor share ──────────────────────────────────

export interface CompetitorShareEntry {
  name: string;
  count: number;
  pct: number;
}

export interface PositionDist {
  lead: number;
  body: number;
  tail: number;
  unknown: number;
}

export interface ShareOfVoice {
  topic_id: number;
  target: string;
  period_days: number;
  brand_count: number;
  competitors_count_total: number;
  saiv_pct: number;
  competitors: CompetitorShareEntry[];
  position_dist: PositionDist;
  optimal_rate_pct: number;
  total_runs: number;
  sample_size: number;
}

export interface CellInsightRec {
  priority: 'P0' | 'P1' | 'P2' | string;
  title: string;
  action: string;
  why: string;
}

export interface CompetitorMention {
  name: string;
  count: number;
  snippet: string;
}

export interface CellInsight {
  id: number;
  topic_id: number;
  query: string;
  engine: EngineId;
  window_start: string;
  window_end: string;
  verdict: 'hit_stable' | 'hit_unstable' | 'near_miss' | 'no_signal' | 'negative_mention' | string;
  summary: string;
  competitors_top3: CompetitorMention[];
  recommendations: CellInsightRec[];
  answer_format: string | null;
  citation_domains: string[];
  evidence_response_ids: number[];
  llm_model: string;
  prompt_version: string;
  generated_at: string;
  feedback: 'helpful' | 'not_helpful' | 'wrong' | null;
}

export interface CellDrawer {
  cell: QueryHitCell;
  evidence: CellEvidence[];
  insight: CellInsight | null;
}

// ─── v1.2 weekly report ────────────────────────────────────────────

export interface BriefingAction {
  priority: string;
  title: string;
  why: string;
  how: string;
}

export interface Briefing {
  id: number;
  topic_id: number;
  period_start: string;
  period_end: string;
  body_md: string;
  kpi_snapshot: Record<string, unknown>;
  top_actions: BriefingAction[];
  delivered_email_at: string | null;
  feedback_score: number | null;
  llm_model: string;
  prompt_version: string;
  generated_at: string;
}

export interface KpiBlock {
  value: number;
  delta_pct: number | null;
  sparkline: number[];
}

export interface TrendPoint {
  date: string;
  values: Partial<Record<EngineId, number>>;
}

export interface DomainCount {
  domain: string;
  count: number;
  pct: number;
}

export interface OwnedSplit {
  owned: number;
  other: number;
  owned_pct: number;
  delta_pct: number | null;
}

export interface Overview {
  topic_id: number;
  period_days: number;
  brand_keywords: string[];
  visibility: KpiBlock;
  citations: KpiBlock;
  growth: KpiBlock;
  engines_covered: KpiBlock;
  engines_total: number;
  trend: TrendPoint[];
  engines: EngineId[];
  top_domains: DomainCount[];
  owned_split: OwnedSplit;
  engine_domain_matrix: Partial<Record<EngineId, Record<string, number>>>;
  engine_factor_matrix: Partial<Record<EngineId, Record<string, number>>>;  // engine -> {GEO factor: count}
}

export interface ClusterBreakdownItem {
  cluster_id: number;
  label: string;
  query_count: number;
  response_count: number;
  mention_count: number;
  mention_rate: number;
  citation_count: number;
}

export interface IntentBreakdown {
  topic_id: number;
  period_days: number;
  brand_keywords: string[];
  clusters: ClusterBreakdownItem[];
  uncategorized: ClusterBreakdownItem;
}

// ── Brand growth page — core metrics + industry benchmarks + competitor substitution evidence ───────
// The six percentages are digit-for-digit identical to the "Report center" (backend delegates to report_metrics_source, latest period · weekly).
// Field names are historical leftovers, see comments for semantics; display names always come from metricLabels in Reports/lang.ts.
export interface PositionBreakdown {
  visible_pct: number;        // Overall exposure rate
  top1_pct: number;           // TOP1 exposure rate
  top3_pct: number;           // TOP3 exposure rate
  accuracy_pct: number;       // Content neutral-positive rate
  source_pct: number;         // Article citation rate
  mention_pct: number;        // Brand share of voice
  seed_coverage_pct: number;  // Seed share — unique to brand growth, full-lifecycle metric definition, not in the report center
}

export interface EngineSlice {
  engine: string;
  breakdown: PositionBreakdown;
  total_queries: number;
}

export interface GroupBreakdown {
  scope: 'query';
  total_queries: number;
  total_engines: number;
  total_cells: number;
  breakdown: PositionBreakdown;
  by_engine: EngineSlice[];
}

export interface PositionBreakdownResp {
  topic_id: number;
  period_days: number;
  industry: string;
  // Compatibility field: same value as query_group; RadarBlock still reads it here
  total_cells: number;
  total_queries: number;
  breakdown: PositionBreakdown;
  industry_baseline: PositionBreakdown | null;
  // Full query group + per-engine slices (for multi-model comparison)
  query_group: GroupBreakdown | null;
  engines: string[];
}

// ── Growth report (first batch run vs. current cumulative) ────────────────────────
// period=0 means: full history (since launch). The brand growth page always passes this.
export const ALL_TIME_PERIOD = 0;

export type GrowthStatus = 'new_hit' | 'improved' | 'steady' | 'regressed' | 'still_miss';

export interface GrowthQueryRow {
  query: string;
  seed?: string | null;
  baseline_hit: boolean;
  baseline_rank: number | null;
  baseline_engines: string[];
  current_hit: boolean;
  current_rank: number | null;
  current_engines: string[];
  status: GrowthStatus;
}

export interface GrowthSnapshot {
  run_at: string | null;
  queries_total: number;
  queries_hit: number;
  hit_rate_pct: number;
  avg_rank: number | null;
  engines_covered: number;
}

export interface GrowthReport {
  topic_id: number;
  target: string;
  generated_at: string;
  has_baseline: boolean;
  baseline: GrowthSnapshot;
  current: GrowthSnapshot;
  rows: GrowthQueryRow[];
}

export interface IndustryBenchmark {
  industry: string;
  sample_size: number;
  breakdown: PositionBreakdown | null;
}

export interface CompetitorSubstitutionItem {
  query: string;
  competitor_name: string;
  competitor_count: number;
  sample_response_id: number;
  sample_snippet: string;
}

export interface AdminAccount {
  id: number;
  email: string;
  name?: string | null;
  // Brand name filled by the client (ai_telemetry_topics.target). The admin profile list shows
  // this first, representing the client's identity in the system; users.name is an admin internal note, used as a fallback.
  brand_target?: string | null;
  is_active: boolean;
  is_admin: boolean;
  topic_count: number;
  has_prompt_extension: boolean;
}

export interface AdminRun {
  run_id: number;
  topic_id: number;
  topic_name: string;
  topic_target: string;
  user_id: number;
  user_email: string;
  started_at: string;
  finished_at: string | null;
  status: 'running' | 'success' | 'failed' | string;
  error: string | null;
  response_count: number;
  hit_count: number;
  error_count: number;
}

export interface CompetitorSubstitutionResp {
  topic_id: number;
  period_days: number;
  competitor_filter: string | null;
  items: CompetitorSubstitutionItem[];
  total: number;
}


export function isMockMode(): boolean {
  return String(import.meta.env.VITE_USE_MOCK_AI_TELEMETRY || '').toLowerCase() === '1';
}

// ── mock state ──────────────────────────────────────────────
let _mockSeq = 100;
const _mockTopics: Topic[] = [
  {
    id: 1,
    user_id: 1,
    name: 'ACME 品牌问询',
    target: 'Acme Cloud',
    target_aliases: ['ACME', 'Acme Cloud China'],
    industry: 'IDC 数据中心',
    queries: ['Acme Cloud怎么样', '国内 IDC 服务商推荐', 'ACME vs <competitor-a>'],
    engines: ['deepseek', 'doubao', 'qwen', 'wenxin', 'yuanbao', 'kimi'],
    enabled: true,
    last_run_at: new Date(Date.now() - 2 * 3600 * 1000).toISOString(),
    last_run_status: 'success',
  },
];

export interface BrowserWorker {
  id: number;
  worker_uid: string;
  hostname: string | null;
  label: string | null;
  region: string | null;
  exit_ip: string | null;
  engines: string[];
  max_concurrency: number;
  status: string;          // online | offline (computed from heartbeat freshness)
  raw_status: string;      // online | draining | disabled | offline
  enabled: boolean;
  version: string | null;
  last_heartbeat_at: string | null;
  done_today: number;
  in_flight: number;
  breaker: Record<string, unknown>;
  /** Hot session state of each engine on this machine (login session/rebuilding/circuit breaker), reported by worker heartbeats */
  hot_sessions?: HotSessionState[];
  /** Quality of the last 10 deliveries; null when no tasks have run */
  quality?: WorkerQuality | null;
  /** Engines removed by the center side (worker_policies in the DB ∪ env WORKER_ENGINE_DENY) */
  denied_engines?: string[];
  /** Reason the egress gate is blocking it right now (with specific numbers). null = not blocked */
  blocked_reason?: string | null;
  /** The center is **not** blocking it, but it isn't picking up work itself (worker process hung). Different from blocked_reason:
   *  that one is "the center actively blocks it", this one is "the center lets it through but it doesn't come". null = normal */
  idle_reason?: string | null;
  /** This machine's bundle version. null = not yet on the bundle self-update channel, needs one manual push (see V-networks/workers/desktop-cn/README.md) */
  bundle_version?: string | null;
  /** Version of worker.py on disk right now (computed at heartbeat). Mismatch with version = files updated, process not yet restarted.
   *  null = this machine runs old code that doesn't report this field. */
  disk_version?: string | null;
  /** When the process last started (= last register). null = never restarted since this center version went live */
  last_register_at?: string | null;
  /** What to do since this machine hasn't upgraded. null = nothing needed. level=action needs a human; info means the upgrade is on its way */
  upgrade_hint?: { level: 'action' | 'info'; code: string; text: string } | null;
  /** This machine's runtime param overrides in the DB */
  policy?: { config: Record<string, unknown>; denied_engines: string[]; proxy_id: number | null };
}

/** The runtime params actually in effect on a machine. Global and per-machine overrides are given separately -- with only the merged result
 *  you can't tell whether a number was set globally or by this machine, and thus where to change it. */
export interface WorkerPolicy {
  worker_uid: string;
  global_config: Record<string, unknown>;
  override: Record<string, unknown>;
  effective: Record<string, unknown>;
  denied_engines: string[];
  denied_from_env: string[];
  proxy_id: number | null;
  note: string | null;
  updated_by: string | null;
  updated_at: string | null;
}

export interface WorkerDiagnostics {
  worker_uid: string;
  days: number;
  done: number;
  failed: number;
  ok_rate: number | null;
  failures: { label: string; n: number; sample: string | null }[];
  failure_keys: Record<string, number>;
  blocked_reason: string | null;
  timeline: { at: string | null; kind: string; detail: Record<string, unknown> }[];
}

export interface HotSessionState {
  engine: string;
  initialized: boolean;
  /** null = not verified yet */
  login_ok: boolean | null;
  query_count: number;
  queries_since_rotate: number;
  rotate_count: number;
  last_rotate_reason: string | null;
  last_rotate_ago_sec: number | null;
  rotate_fail_streak: number;
  breaker_open: boolean;
  breaker_remain_sec: number;
  last_used_ago_sec: number | null;
}

export interface WorkerQuality {
  n: number;
  done: number;
  avg_len: number;
  avg_cites: number;
}

export interface WorkerQueueStats {
  queue: Record<string, Record<string, number>>;   // engine → {status: count}
  used_today: Record<string, number>;              // engine -> calls initiated today
  daily_cap: Record<string, number | null>;        // engine -> daily cap (null = unlimited)
}

export interface EngineProxy {
  id: number;
  label: string;
  proxy_url: string;              // Password masked
  note?: string | null;
  disabled: boolean;
  bound_count: number;           // Number of accounts bound
  last_tested_at?: string | null;
  last_exit_ip?: string | null;
  last_country?: string | null;
}

export interface EngineSessionRow {
  id: number;
  engine: string;
  label: string | null;
  account_handle: string | null;   // Nickname/masked phone number captured by the collector
  status: string;            // active | quarantined | expired
  use_count: number;
  captcha_count: number;
  last_used_at: string | null;
  captured_at: string | null;
  expires_at: string | null;
  last_fail_type: string | null;
  used_today?: number;
  auth_type?: string | null;       // password/apikey/qr/cookie
  egress?: string | null;          // proxy/host
  proxy_override?: string | null;  // Full proxy string bound to this account (non-empty = dedicated IP bound)
  proxy_id?: number | null;        // Reference to a proxy library entry (non-empty = bound from the proxy library)
  exit_ip?: string | null;         // Actual egress IP (reported by worker check-in)
  exit_ip_at?: string | null;
  daily_cap?: number | null;       // Per-account override (null = use the engine default)
  effective_cap?: number;          // Actual effective cap (0 = unlimited)
  remaining?: number | null;       // Remaining quota today (null = unlimited)
  priority?: number;               // Scheduling priority, lower first
  disabled?: boolean;              // Whether manually disabled
  rate_limited?: boolean;          // Whether in rate-limit cooldown
}

export interface SelectorProposal {
  input_sels?: string[];
  send_sels?: string[];
  answer_sels?: string[];
  chat_api?: string;
  url?: string;
}

export interface EngineHealth {
  engine: string;
  ok: boolean;
  ans_len: number;
  cites: number;
  error: string | null;
}

export interface EngineIpGroup {
  exit_ip: string;
  engine: string;
  accounts: number;
  used_today: number;
  ip_account_total: number;   // Total accounts across engines on this IP (density)
  cooling: number;            // Accounts in rate-limit cooldown on this egress
  muted_24h: number;          // Accounts rate-limited/muted within 24h (≥2 = this egress has been flagged by the platform)
}

export interface EngineSessionPage {
  items: EngineSessionRow[];
  total: number;             // Count after filtering (drives pagination)
  grand_total: number;       // Count of the whole pool
  active_total: number;      // Whole pool with status=active and not expired (truly available for check-out)
  engines: string[];         // Engines appearing in the pool (for the filter dropdown)
  quarantined_total?: number;             // Total offline (quarantined), needs handling
  quarantined_by_engine?: Record<string, number>;
  page: number;
  page_size: number;
}

async function request<T>(
  method: string, path: string, token: string, body?: unknown,
): Promise<T> {
  const init: RequestInit = {
    method,
    headers: localizedHeaders({
      Authorization: `Bearer ${token}`,
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    }),
  };
  if (body !== undefined) init.body = JSON.stringify(body);
  const resp = await fetch(`${API_BASE}/ai-telemetry${path}`, init);
  if (!resp.ok) {
    const msg = await readApiError(resp, `Request ${method} ${path} failed`);
    throw new Error(msg);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json() as Promise<T>;
}

// Topic progress (GET /topics/{id}/progress). Backend geo/services/topic_progress.py is the single source of truth,
// and this is just its shape -- don't re-derive stage judgments/copy on the frontend.
export type TopicStage =
  | 'no_profile' | 'no_queries' | 'insufficient' | 'ready' | 'running' | 'done'
  | 'paused' | 'off';

// Shape of batch_run in POST /topics/{id}/run and POST solution/generate
export interface BatchRunResult {
  status: string;              // started / nothing_new / insufficient_balance / unavailable / cancelled
  topic_id: number;
  run_id?: number | null;
  cells?: number | null;
  message?: string;
  detail?: string;
}

export interface RunQuote {
  topic_id: number;
  only_new: boolean;
  total_cells: number;         // The whole matrix
  cells: number;               // Cells to run this time (with only_new = those never succeeded yet)
  price_cents: number;
  need_cents: number;
  balance_cents: number;
  chargeable: boolean;
}

export interface TopicProgress {
  topic_id: number;
  topic_name: string;
  stage: TopicStage;
  label: string;
  next_action: { kind: string; text: string };
  queries: { active: number; total: number };
  engines: number;
  /** This topic's seed query texts -- lets users match "the seeds I chose" with "the hundreds of queries running" */
  seeds: string[];
  cost: {
    cells: number; price_cents: number; need_cents: number;
    balance_cents: number; chargeable: boolean; affordable: boolean;
  };
  run: {
    id: number; status: string; error: string | null;
    started_at: string | null; finished_at: string | null;
    cells_done: number; cells_total: number; percent: number;
    /** This round's money: how much was charged / refunded at closing / net charged (credits). */
    charged_cents: number; refunded_cents: number; net_cents: number;
    /** Cells actually run / errored per engine this round. Overall progress can hide "one engine failed the entire round" */
    engines: { engine: string; ok: number; error: number }[];
  } | null;
  // manual = not in periodic batch runs (tier off), runs only when the button on the plan page is clicked -- the default for new topics since 2026-09-21
  schedule: { tier: string; enabled: boolean; paused_until: string | null; manual?: boolean };
}

// External gateway: tenants / call logs (for the ops management page)
export interface GatewayTenant {
  id: number; name: string; status: string; tier: string;
  engines: string[]; daily_quota_default: number; daily_quota: Record<string, number>;
  credit_balance: number; active_keys: number; calls_today: number;
  billable_total: number; credits_spent_total: number; created_at: string | null;
  user_id?: number | null; bound_email?: string | null;
  // Purpose: customer = external customer / internal = own system / test = integration testing.
  // Non-empty source_key = an internal tenant auto-created by the system (internal:batch etc.), not to be edited manually.
  kind?: string; source_key?: string | null; created_by?: string | null;
}
export interface GatewayJob {
  id: number; tenant_id: string | null; engine: string; query: string;
  status: string; error: string | null; created_at: string | null; finished_at: string | null;
  answer?: string; citations?: { url?: string; domain?: string; title?: string }[];
  source_url?: string | null; video_url?: string | null;
  // Source: batch = internal batch run (enqueued via the gateway, carrying sentiment-domain run/topic) / api = external tenant call.
  // Both kinds of calls now share a table (browser_jobs) and can't be separated without this field. When an old backend doesn't return it, it's undefined,
  // and the frontend shows it as api -- back then there were only external calls anyway.
  source?: 'batch' | 'api';
  run_id?: number | null; topic_id?: number | null;
}

// Seed expansion input — shared by suggestQueries (regular) and suggestQueriesStream (SSE), avoiding drift between the two.
export interface SuggestQueryArgs {
  seed: string; count: number;
  target?: string; aliases?: string[]; industry?: string;
  // Entity type — drives the style of expanded queries (service_tool / manufacturer / brand_owner / other / '')
  entity_type?: string;
  // Service region filled in the profile by the user; when non-empty the prompt locks the location dimension to that region.
  service_geo?: string;
  // 4-dimension scenes; omitted defaults to fanning out across all 4.
  scenes?: SceneType[];
  // Output count per scene (≤50 per dimension, ≤200 across 4).
  countPerScene?: number;
  // Profile fields (injected for intent / brand scenes, ignored for search / qa).
  profile_cases?: string[];
  core_credentials?: string[];
  brand_diff_tags?: string[];
  core_service_overview?: string;
  // Product list from section 3 of the materials; a seed matching one of them uses product mode (backend find_product)
  products?: BrandProduct[];
}

// The regular and streaming versions send the same body, parsed uniformly by backend _parse_suggest_args.
function buildSuggestBody(args: SuggestQueryArgs): Record<string, unknown> {
  return {
    seed: args.seed,
    count: args.count,
    // Backend v2 prefers count_per_scene; otherwise falls back to count/4, keeping old callers' semantics stable.
    ...(args.countPerScene ? { count_per_scene: args.countPerScene } : {}),
    ...(args.scenes ? { scenes: args.scenes } : {}),
    target: args.target || '',
    aliases: args.aliases || [],
    industry: args.industry || '',
    entity_type: args.entity_type || '',
    service_geo: args.service_geo || '',
    profile_cases: args.profile_cases || [],
    core_credentials: args.core_credentials || [],
    brand_diff_tags: args.brand_diff_tags || [],
    core_service_overview: args.core_service_overview || '',
    products: args.products || [],
  };
}

// ─── Flywheel P1-D: source strategy (why cited + placement outlet recommendations) ────────────
export interface SourceStrategyTopSource {
  domain: string; source_type: string; count: number;
  engines: string[]; sample_titles: string[];
}
export interface SourceStrategyPerQuery {
  query: string; hit_pct: number;
  top_domains: { domain: string; count: number }[];
  answer_formats: Record<string, number>;
}
export interface SourceStrategy {
  topic_id: number;
  status: 'idle' | 'generating' | 'ready' | 'failed';
  source_stats: {
    responses_total?: number;
    top_sources?: SourceStrategyTopSource[];
    source_type_share?: { source_type: string; count: number; pct: number }[];
    per_engine?: {
      engine: string; responses: number; hit_rate_pct: number;
      top_domains: { domain: string; count: number }[];
      answer_formats: Record<string, number>;
    }[];
    per_query?: SourceStrategyPerQuery[];
    unhit_queries?: string[];
  };
  analysis: {
    why_cited?: { pattern: string; evidence: string; implication: string }[];
    engine_differences?: { engine: string; preference: string }[];
    query_source_insights?: { query: string; why_these_sources: string; gap_plan: string }[];
    content_guidelines?: string[];
  };
  recommendations: { platform: string; priority: string; reason: string; content_type: string }[];
  llm_model: string;
  error?: string | null;
  generated_at?: string | null;
}

export const aiTelemetryApi = {
  async getSourceStrategy(topicId: number, token: string): Promise<SourceStrategy> {
    return request<SourceStrategy>('GET', `/topics/${topicId}/source-strategy`, token);
  },
  async generateSourceStrategy(topicId: number, token: string): Promise<SourceStrategy> {
    return request<SourceStrategy>('POST', `/topics/${topicId}/source-strategy/generate`, token);
  },

  // When an agency switches to an enterprise, the topic scope must narrow accordingly -- that's what the second parameter is for.
  // This signature was added once in 80e06190, then a merge conflict resolved it back to the old version (only the definition reverted,
  // the five 2-argument call sites remained), and `npm run build`'s tsc failed outright:
  // from then on the frontends of develop and release/cn couldn't build, and every CN production deployment got stuck at
  // "frontend build failed" and rolled back -- and since tsc -b is incremental, it wasn't visible locally with a cache.
  async listTopics(token: string, enterpriseUserId?: number | null): Promise<Topic[]> {
    if (isMockMode()) return Promise.resolve([..._mockTopics]);
    const qs = enterpriseUserId ? `?enterprise_user_id=${encodeURIComponent(String(enterpriseUserId))}` : '';
    return request<Topic[]>('GET', `/topics${qs}`, token);
  },

  // Single topic metadata — owners fetch their own; admins can fetch any topic (used by the brand growth panel to resolve non-owned topics via ?topic=).
  async getTopic(id: number, token: string): Promise<Topic> {
    if (isMockMode()) {
      const t = _mockTopics.find(t => t.id === id);
      if (!t) throw new Error('topic not found');
      return Promise.resolve(t);
    }
    return request<Topic>('GET', `/topics/${id}`, token);
  },

  async createTopic(payload: TopicPayload, token: string): Promise<Topic> {
    if (isMockMode()) {
      const t: Topic = {
        id: _mockSeq++, user_id: payload.enterprise_user_id ?? 1, ...payload,
        last_run_at: null, last_run_status: null,
      };
      _mockTopics.push(t);
      return Promise.resolve(t);
    }
    return request<Topic>('POST', '/topics', token, payload);
  },

  // Step1 "Next" persists to DB right away: only brand materials + basic fields are saved, queries/engines may be empty, and the backend creates a draft.
  // Returns the topic with an id for later steps to update incrementally, avoiding data loss from deferring a single save to the end.
  async createDraftTopic(payload: TopicDraftPayload, token: string): Promise<Topic> {
    if (isMockMode()) {
      const t: Topic = {
        id: _mockSeq++,
        user_id: payload.enterprise_user_id ?? 1,
        name: payload.name, target: payload.target,
        target_aliases: payload.target_aliases, industry: payload.industry,
        queries: [], engines: payload.engines, enabled: payload.enabled,
        profile: payload.profile,
        last_run_at: null, last_run_status: null,
      } as Topic;
      _mockTopics.push(t);
      return Promise.resolve(t);
    }
    return request<Topic>('POST', '/topics/draft', token, payload);
  },

  // Batch-run scheduling overview: all topic tiers + per-engine capacity (check whether capacity can handle it before changing tiers)
  async getScheduleOverview(token: string): Promise<ScheduleOverview> {
    return request<ScheduleOverview>('GET', '/admin/schedule-overview', token);
  },

  // Batch-run tiers: take effect at the next drip tick (the scheduler reads the DB on every tick), no service restart needed
  async patchTopicSchedule(
    id: number, payload: TopicSchedulePatch, token: string,
  ): Promise<Topic> {
    return request<Topic>('PATCH', `/admin/topics/${id}/schedule`, token, payload);
  },

  async updateTopic(id: number, payload: TopicPayload, token: string): Promise<Topic> {
    if (isMockMode()) {
      const i = _mockTopics.findIndex(t => t.id === id);
      if (i >= 0) _mockTopics[i] = { ..._mockTopics[i], ...payload };
      return Promise.resolve(_mockTopics[i]);
    }
    return request<Topic>('PUT', `/topics/${id}`, token, payload);
  },

  async deleteTopic(id: number, token: string): Promise<void> {
    if (isMockMode()) {
      const i = _mockTopics.findIndex(t => t.id === id);
      if (i >= 0) _mockTopics.splice(i, 1);
      return Promise.resolve();
    }
    return request<void>('DELETE', `/topics/${id}`, token);
  },

  // The client adds seed prompts. **No review** -- approved once persisted, runs directly in the next round (2026-09-09).
  async submitSeedPrompt(topicId: number, text: string, token: string): Promise<Topic> {
    return request<Topic>('POST', `/topics/${topicId}/seed-prompts`, token, { text });
  },

  // Admin deletes a seed prompt: the seed is removed from the list, and the prompts expanded from it are soft-deleted too (no more batch runs, not in reports).
  async deleteSeedPrompt(topicId: number, text: string, token: string): Promise<Topic> {
    return request<Topic>('POST', `/topics/${topicId}/seed-prompts/delete`, token, { text });
  },

  async suggestQueries(
    args: SuggestQueryArgs,
    token: string,
  ): Promise<{ seed: string; queries: QueryCandidate[]; clusters: ClusterMeta[] }> {
    if (isMockMode()) {
      // mock: assemble args.count fake candidates + 5 clusters + descending scores, enough for debugging the frontend picker
      const stems = ['是什么', '怎么样', '推荐', '对比', '替代方案', '价格', '评价', '案例', '使用场景', '行业应用'];
      const scenesArr: SceneType[] = ['search', 'qa', 'intent', 'brand'];
      const out: QueryCandidate[] = [];
      const clusterLabels = ['认知与定义', '对比与差异', '价格与费用', '案例与口碑', '使用与场景'];
      for (let i = 0; out.length < args.count; i++) {
        out.push({
          text: `${args.seed} ${stems[i % stems.length]} ${Math.floor(i / stems.length) + 1}`,
          score: Math.max(20, 90 - i),
          sources: i % 4 === 0 ? ['suggest:baidu'] : ['llm:deepseek'],
          cluster_id: i % 5,
          scene: scenesArr[i % scenesArr.length],
        });
      }
      const clusters: ClusterMeta[] = clusterLabels.map((l, i) => ({
        cluster_id: i,
        label: l,
        size: out.filter(q => q.cluster_id === i).length,
        medoid_index: out.findIndex(q => q.cluster_id === i),
      }));
      return Promise.resolve({ seed: args.seed, queries: out, clusters });
    }
    return request<{ seed: string; queries: QueryCandidate[]; clusters: ClusterMeta[] }>(
      'POST', '/suggest-queries', token, buildSuggestBody(args),
    );
  },

  // SSE streaming expansion: call back once per finished scene, so the frontend renders as it receives without waiting for the whole batch.
  // onScene(scene, queries): candidates for a scene arrived (may be empty; with error it means that scene failed).
  // Resolves only after the whole stream ends; rejects on errors midway. Prompts/parsing are identical to suggestQueries.
  async suggestQueriesStream(
    args: SuggestQueryArgs,
    token: string,
    onScene: (scene: SceneType, queries: QueryCandidate[], error?: string | null) => void,
    // First meta frame: which product the seed hit (null = none). Optional; old callers that don't pass it work as before.
    onMeta?: (meta: { matched_product: MatchedProduct | null }) => void,
  ): Promise<void> {
    if (isMockMode()) {
      // mock: reuse the regular version's result directly, calling back group by group per scene to simulate streaming.
      const { queries } = await this.suggestQueries(args, token);
      for (const sc of (['search', 'qa', 'intent', 'brand'] as SceneType[])) {
        onScene(sc, queries.filter(q => q.scene === sc));
      }
      return;
    }
    const resp = await fetch(`${API_BASE}/ai-telemetry/suggest-queries/stream`, {
      method: 'POST',
      headers: localizedHeaders({
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      }),
      body: JSON.stringify(buildSuggestBody(args)),
    });
    if (!resp.ok || !resp.body) {
      throw new Error(await readApiError(resp, 'POST /suggest-queries/stream failed'));
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      // SSE events are separated by blank lines (\n\n)
      const parts = buf.split('\n\n');
      buf = parts.pop() || '';
      for (const part of parts) {
        const dataLine = part.split('\n').find(l => l.startsWith('data:'));
        if (!dataLine) continue;
        const json = dataLine.slice(5).trim();
        if (!json) continue;
        let obj: {
          type?: string; scene?: SceneType; queries?: QueryCandidate[]; error?: string | null;
          matched_product?: MatchedProduct | null;
        };
        try { obj = JSON.parse(json); } catch { continue; }
        if (obj.type === 'meta') {
          onMeta?.({ matched_product: obj.matched_product ?? null });
          continue;
        }
        if (obj.type === 'scene' && obj.scene) {
          onScene(obj.scene, Array.isArray(obj.queries) ? obj.queries : [], obj.error ?? null);
        }
      }
    }
  },

  async runNow(payload: TopicPayload, token: string): Promise<RunNowResult[]> {
    if (isMockMode()) {
      return Promise.resolve(
        payload.engines.flatMap(engine =>
          payload.queries.map(query => ({
            engine,
            query,
            answer: `[mock] ${engine} 关于 "${query}" 的回答...`,
            citations: [{ url: 'https://example.com', domain: 'example.com', title: 'Example' }],
            error: null,
          }))
        )
      );
    }
    return request<RunNowResult[]>('POST', '/topics/run-now', token, payload);
  },

  // Where the topic is at -- status / next step / cost of this round / which cell it has reached.
  // Always use stage as the criterion, never base logic on label: label is a human-readable sentence and will change.
  async getProgress(topicId: number, token: string): Promise<TopicProgress> {
    return request<TopicProgress>('GET', `/topics/${topicId}/progress`, token);
  },

  // onlyNew=true: only run cells that never succeeded (the plan page's "New batch run"). When the query list hasn't changed the backend returns
  // status='nothing_new', creating no run and charging nothing.
  async triggerRun(
    topicId: number, token: string, opts?: { onlyNew?: boolean },
  ): Promise<BatchRunResult> {
    if (isMockMode()) return Promise.resolve({ status: 'started', topic_id: topicId });
    const qs = opts?.onlyNew ? '?only_new=true' : '';
    return request('POST', `/topics/${topicId}/run${qs}`, token);
  },

  // How many credits a round costs (quote only, no charge). onlyNew (default true) = only count cells never succeeded,
  // same metric definition as the round run along with "Generate plan" / "New batch run".
  async getRunQuote(
    topicId: number, token: string, onlyNew = true,
  ): Promise<RunQuote> {
    if (isMockMode()) {
      return Promise.resolve({
        topic_id: topicId, only_new: onlyNew, total_cells: 0, cells: 0,
        price_cents: 0, need_cents: 0, balance_cents: 0, chargeable: false,
      });
    }
    return request<RunQuote>('GET', `/topics/${topicId}/run-quote?only_new=${onlyNew}`, token);
  },

  async listRuns(topicId: number, token: string, limit = 20): Promise<RunSummary[]> {
    if (isMockMode()) return Promise.resolve([]);
    return request<RunSummary[]>('GET', `/topics/${topicId}/runs?limit=${limit}`, token);
  },

  async listResponses(runId: number, token: string): Promise<ResponseRow[]> {
    if (isMockMode()) return Promise.resolve([]);
    return request<ResponseRow[]>('GET', `/runs/${runId}/responses`, token);
  },

  // Overview of all batch runs for a topic (account) of the current user -- for the task monitor page (owner check, all time)
  async listTopicRuns(
    topicId: number, token: string,
    opts: { day?: string; status?: string; limit?: number; offset?: number } = {},
  ): Promise<AdminRun[]> {
    if (isMockMode()) return Promise.resolve([]);
    const params = new URLSearchParams();
    if (opts.day) params.set('day', opts.day);
    if (opts.status) params.set('status', opts.status);
    if (opts.limit !== undefined) params.set('limit', String(opts.limit));
    if (opts.offset !== undefined) params.set('offset', String(opts.offset));
    const qs = params.toString();
    return request<AdminRun[]>('GET', `/topics/${topicId}/run-results${qs ? `?${qs}` : ''}`, token);
  },

  async getOverview(topicId: number, periodDays: number, token: string): Promise<Overview> {
    if (isMockMode()) {
      const trend = Array.from({ length: periodDays }, (_, i) => ({
        date: new Date(Date.now() - (periodDays - 1 - i) * 86400000).toISOString().slice(0, 10),
        values: { deepseek: Math.floor(Math.random() * 10), qwen: Math.floor(Math.random() * 8) },
      }));
      return Promise.resolve({
        topic_id: topicId, period_days: periodDays,
        brand_keywords: ['mock'],
        visibility: { value: 78, delta_pct: 12, sparkline: trend.map(t => t.values.deepseek || 0) },
        citations: { value: 1248, delta_pct: 16, sparkline: trend.map(t => t.values.deepseek || 0) },
        growth: { value: 32, delta_pct: null, sparkline: [] },
        engines_covered: { value: 5, delta_pct: null, sparkline: [] },
        engines_total: 10,
        trend,
        engines: ['deepseek', 'qwen'],
        top_domains: [
          { domain: 'zhihu.com', count: 42, pct: 18 },
          { domain: 'baike.baidu.com', count: 28, pct: 12 },
        ],
        owned_split: { owned: 24, other: 200, owned_pct: 10.7, delta_pct: 2.1 },
        engine_domain_matrix: { deepseek: { 'zhihu.com': 8 }, qwen: { 'baike.baidu.com': 5 } },
        engine_factor_matrix: {
          deepseek: { list_mention: 12, reviews: 5, social: 8, other: 6 },
          qwen: { list_mention: 7, directory: 4, social: 5, other: 3 },
        },
      });
    }
    return request<Overview>('GET', `/topics/${topicId}/overview?period=${periodDays}`, token);
  },

  // ── v1.3 SAIV / competitor share / preference rate / hit position ────────
  async getShareOfVoice(
    topicId: number, periodDays: number, token: string,
  ): Promise<ShareOfVoice> {
    if (isMockMode()) return Promise.resolve(_mockSoV(topicId, periodDays));
    return request<ShareOfVoice>('GET', `/topics/${topicId}/share-of-voice?period=${periodDays}`, token);
  },

  // Group and aggregate this period's responses by the intent clusters formed on the picker side
  async getIntentBreakdown(
    topicId: number, periodDays: number, token: string,
  ): Promise<IntentBreakdown> {
    if (isMockMode()) {
      const mk = (cid: number, label: string, qc: number, rc: number, mc: number, cc: number) => ({
        cluster_id: cid, label, query_count: qc, response_count: rc,
        mention_count: mc,
        mention_rate: rc ? Math.round(mc / rc * 1000) / 1000 : 0,
        citation_count: cc,
      });
      return Promise.resolve({
        topic_id: topicId, period_days: periodDays, brand_keywords: ['mock'],
        clusters: [
          mk(0, '认知与定义', 12, 84, 31, 96),
          mk(1, '对比与差异', 10, 70, 18, 64),
          mk(2, '价格与费用', 8, 56, 38, 72),
          mk(3, '案例与口碑', 6, 42, 25, 50),
          mk(4, '使用与场景', 4, 28, 9, 22),
        ],
        uncategorized: mk(-1, 'uncategorized', 0, 0, 0, 0),
      });
    }
    return request<IntentBreakdown>(
      'GET', `/topics/${topicId}/intent-breakdown?period=${periodDays}`, token,
    );
  },

  // ── v1 citation tracking ──────────────────────────
  async getTrackingMatrix(topicId: number, token: string): Promise<TrackingMatrix> {
    if (isMockMode()) return Promise.resolve(_mockMatrix(topicId));
    return request<TrackingMatrix>('GET', `/topics/${topicId}/tracking-matrix`, token);
  },

  async getGrowthReport(topicId: number, token: string): Promise<GrowthReport> {
    return request<GrowthReport>('GET', `/topics/${topicId}/growth-report`, token);
  },

  async getCellDrawer(
    topicId: number, query: string, engine: EngineId, token: string,
  ): Promise<CellDrawer> {
    if (isMockMode()) return Promise.resolve(_mockDrawer(topicId, query, engine));
    const qs = new URLSearchParams({ query, engine }).toString();
    return request<CellDrawer>('GET', `/topics/${topicId}/cells/drawer?${qs}`, token);
  },

  // ── v1.1 LLM diagnosis ─────────────────────────
  async fetchCellInsight(
    topicId: number, query: string, engine: EngineId, token: string, force = false,
  ): Promise<CellInsight> {
    if (isMockMode()) return Promise.resolve(_mockInsight(topicId, query, engine));
    const qs = new URLSearchParams({ query, engine, force: String(force) }).toString();
    return request<CellInsight>('POST', `/topics/${topicId}/cells/insight?${qs}`, token);
  },

  async postCellInsightFeedback(
    insightId: number, feedback: 'helpful' | 'not_helpful' | 'wrong', token: string,
  ): Promise<void> {
    if (isMockMode()) return Promise.resolve();
    return request<void>('POST', `/cell-insights/${insightId}/feedback`, token, { feedback });
  },

  // ── v1.2 weekly report ─────────────────────────────
  async listBriefings(topicId: number, token: string, limit = 12): Promise<Briefing[]> {
    if (isMockMode()) return Promise.resolve([_mockBriefing(topicId)]);
    return request<Briefing[]>('GET', `/topics/${topicId}/briefings?limit=${limit}`, token);
  },

  async getBriefing(briefingId: number, token: string): Promise<Briefing> {
    if (isMockMode()) return Promise.resolve(_mockBriefing(0, briefingId));
    return request<Briefing>('GET', `/briefings/${briefingId}`, token);
  },

  async triggerBriefing(topicId: number, token: string): Promise<Briefing> {
    if (isMockMode()) return Promise.resolve(_mockBriefing(topicId));
    return request<Briefing>('POST', `/topics/${topicId}/briefings/generate`, token);
  },

  async postBriefingFeedback(briefingId: number, score: number, token: string): Promise<void> {
    if (isMockMode()) return Promise.resolve();
    return request<void>('POST', `/briefings/${briefingId}/feedback`, token, { score });
  },

  // ── Brand growth page — Phase 1 backend-aggregated ground-truth endpoints ─────────────
  async getPositionBreakdown(
    topicId: number, periodDays: number, token: string,
  ): Promise<PositionBreakdownResp> {
    return request<PositionBreakdownResp>(
      'GET', `/topics/${topicId}/position-breakdown?period=${periodDays}`, token,
    );
  },

  async getIndustryBenchmark(
    industry: string, periodDays: number, token: string,
  ): Promise<IndustryBenchmark> {
    const qs = new URLSearchParams({ industry, period: String(periodDays) }).toString();
    return request<IndustryBenchmark>('GET', `/benchmarks/industry?${qs}`, token);
  },

  async getCompetitorSubstitutions(
    topicId: number, periodDays: number, token: string,
    opts: { competitor?: string; limit?: number } = {},
  ): Promise<CompetitorSubstitutionResp> {
    const params = new URLSearchParams({ period: String(periodDays) });
    if (opts.competitor) params.set('competitor', opts.competitor);
    if (opts.limit) params.set('limit', String(opts.limit));
    return request<CompetitorSubstitutionResp>(
      'GET', `/topics/${topicId}/competitor-substitutions?${params.toString()}`, token,
    );
  },

  // ── Admin cross-user management ─────────────────────────────────
  async adminListAccounts(token: string): Promise<AdminAccount[]> {
    return request<AdminAccount[]>('GET', '/admin/accounts', token);
  },

  // ── Scheduling center / Worker management ───────────────────────────
  async adminListWorkers(token: string): Promise<BrowserWorker[]> {
    return request<BrowserWorker[]>('GET', '/admin/workers', token);
  },
  async adminWorkersQueueStats(token: string): Promise<WorkerQueueStats> {
    return request<WorkerQueueStats>('GET', '/admin/workers-queue-stats', token);
  },
  async adminWorkerAction(
    workerUid: string, action: 'enable' | 'disable' | 'drain', token: string,
  ): Promise<{ ok: boolean; status: string }> {
    return request('POST', `/admin/workers/${workerUid}/${action}`, token);
  },
  async adminWorkerPolicy(token: string, workerUid: string): Promise<WorkerPolicy> {
    return request<WorkerPolicy>('GET', `/admin/workers/${workerUid}/policy`, token);
  },
  async adminSaveWorkerPolicy(
    token: string, workerUid: string,
    body: { config?: Record<string, unknown>; denied_engines?: string[];
            proxy_id?: number | null; note?: string },
  ): Promise<WorkerPolicy> {
    return request<WorkerPolicy>('PUT', `/admin/workers/${workerUid}/policy`, token, body);
  },
  async adminWorkerDiagnostics(
    token: string, workerUid: string, days = 3,
  ): Promise<WorkerDiagnostics> {
    return request<WorkerDiagnostics>(
      'GET', `/admin/workers/${workerUid}/diagnostics?days=${days}`, token);
  },
  /** Remove ghost rows (long-offline registration records). Historical tasks are kept */
  async adminForgetWorker(token: string, workerUid: string): Promise<{ ok: boolean }> {
    return request('POST', `/admin/workers/${workerUid}/forget`, token);
  },
  // Add account · password authorization: the server logs in automatically (via proxy) and captures the login session into the pool
  async adminAuthorizeAccount(
    token: string, engine: string, identifier: string, password: string,
  ): Promise<{ ok: boolean; error?: string | null; account_handle?: string; uploaded?: boolean }> {
    return request('POST', '/admin/authorize-account', token, { engine, identifier, password });
  },

  // QR scan assisted authorization: start (returns the QR code) + poll (joins the pool on successful login)
  async adminAuthorizeQrStart(
    token: string, engine: string,
  ): Promise<{ session_id?: string; qr_image?: string; error?: string | null }> {
    return request('POST', '/admin/authorize-qr/start', token, { engine });
  },
  async adminAuthorizeQrPoll(
    token: string, sessionId: string,
  ): Promise<{ status: string; qr_image?: string; uploaded?: boolean; error?: string | null }> {
    return request('POST', '/admin/authorize-qr/poll', token, { session_id: sessionId });
  },

  // Phone verification code assisted authorization (qwen / kimi): send code -> human enters the code -> log in and join the pool.
  // status=captcha: hit a slider, the page is open on the server; captcha_manual=true (kimi) means a slider always appears, a human drags it via VNC then enters the code
  async adminAuthorizeSmsStart(
    token: string, engine: string, phone: string, countryCode = '86',
  ): Promise<{ session_id?: string; status?: string; screenshot?: string | null; error?: string | null; country_ok?: boolean; captcha_manual?: boolean }> {
    return request('POST', '/admin/authorize-sms/start', token, { engine, phone, country_code: countryCode });
  },
  async adminAuthorizeSmsSubmit(
    token: string, sessionId: string, code: string,
  ): Promise<{ status: string; uploaded?: boolean; account_handle?: string; screenshot?: string | null; error?: string | null }> {
    return request('POST', '/admin/authorize-sms/submit', token, { session_id: sessionId, code });
  },
  async adminAuthorizeSmsPoll(
    token: string, sessionId: string, cancel = false,
  ): Promise<{ status: string; screenshot?: string | null; uploaded?: boolean; error?: string | null; captcha?: boolean }> {
    return request('POST', '/admin/authorize-sms/poll', token, { session_id: sessionId, cancel });
  },

  async adminListEngineSessions(
    token: string, page = 1, pageSize = 20, engine = '', status = '',
  ): Promise<EngineSessionPage> {
    const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
    if (engine) params.set('engine', engine);
    if (status) params.set('status', status);
    return request<EngineSessionPage>('GET', `/admin/engine-sessions?${params}`, token);
  },

  // Worker management: engine self-heal — probe + LLM propose selectors (propose only)
  async adminHealEngine(
    token: string, engine: string,
  ): Promise<{
    engine: string; probe_error: string | null; proposal: SelectorProposal | null;
    validation: { ans_len: number; cites: number; error: string | null } | null;
    validated: boolean; llm_raw: string;
  }> {
    return request('POST', `/admin/heal-engine/${engine}`, token);
  },
  // Worker management: write reviewed proposals into the center config (applies to the whole fleet)
  async adminHealEngineApply(
    token: string, engine: string, patch: SelectorProposal,
  ): Promise<{ engine: string; applied: SelectorProposal }> {
    return request('POST', `/admin/heal-engine/${engine}/apply`, token, patch);
  },

  // Worker management: latest results of the engine health sentinel
  async adminEngineHealth(
    token: string,
  ): Promise<{ checked_at: string | null; engines: EngineHealth[] }> {
    return request('GET', '/admin/engine-health', token);
  },

  // Worker management: aggregated by egress IP (accounts per IP / usage today / density)
  async adminEngineSessionsByIp(
    token: string,
  ): Promise<{ groups: EngineIpGroup[]; ip_count: number }> {
    return request('GET', '/admin/engine-sessions/by-ip', token);
  },

  // Worker management: enable/disable accounts / change per-account daily cap (-1 = clear override) / change priority
  async adminPatchEngineSession(
    token: string, sessionId: number,
    patch: { disabled?: boolean; daily_cap?: number; priority?: number; proxy_override?: string; proxy_id?: number },
  ): Promise<{ id: number; daily_cap: number | null; priority: number; disabled: boolean; proxy_override?: string | null; proxy_id?: number | null }> {
    return request('PATCH', `/admin/engine-sessions/${sessionId}`, token, patch);
  },
  // Self-test any proxy string before binding: returns egress IP + country
  async adminTestProxy(
    token: string, proxy: string,
  ): Promise<{ ok: boolean; ip: string; country: string; status?: number; error?: string }> {
    return request('POST', '/admin/engine-sessions/test-proxy', token, { proxy });
  },

  // ── Proxy library (centrally registered proxies, referenced by accounts via proxy_id) ─────────────
  async adminListProxies(token: string): Promise<{ items: EngineProxy[] }> {
    return request('GET', '/admin/proxies', token);
  },
  async adminCreateProxy(
    token: string, payload: { label: string; proxy_url: string; note?: string },
  ): Promise<{ id: number; label: string }> {
    return request('POST', '/admin/proxies', token, payload);
  },
  async adminTestProxyById(
    token: string, proxyId: number,
  ): Promise<{ ok: boolean; ip: string; country: string; status?: number; error?: string }> {
    return request('POST', `/admin/proxies/${proxyId}/test`, token);
  },
  async adminDeleteProxy(token: string, proxyId: number): Promise<{ ok: boolean; unbound: number }> {
    return request('DELETE', `/admin/proxies/${proxyId}`, token);
  },

  // ── One-click entry to the collection network admin console (admin): once the backend verifies, it hands back a keyed URL for the frontend to window.open ──
  async adminVnetConsole(token: string): Promise<{ url: string }> {
    return request('GET', '/admin/vnet-console', token);
  },

  // ── External gateway operations management ─────────────────────────────────
  async adminGatewayTenants(token: string): Promise<{ tenants: GatewayTenant[] }> {
    return request('GET', '/admin/gateway/tenants', token);
  },
  async adminGatewayCreateTenant(
    payload: {
      name: string; tier?: string; engines?: string[]; daily_quota_default?: number; daily_quota?: Record<string, number>;
      kind?: string;
      // Account opening, one of three: don't bind / email+password opens a new platform account / user_id binds an existing account
      email?: string; password?: string; user_id?: number;
    },
    token: string,
  ): Promise<{ tenant_id: number; key_id: number; api_key: string; bound_email?: string }> {
    return request('POST', '/admin/gateway/tenants', token, payload);
  },
  async adminGatewayUpdateTenant(
    tenantId: number,
    payload: { status?: string; tier?: string; kind?: string; engines?: string[]; daily_quota_default?: number; daily_quota?: Record<string, number>; user_id?: number; unbind_user?: boolean },
    token: string,
  ): Promise<{ ok: boolean }> {
    return request('PATCH', `/admin/gateway/tenants/${tenantId}`, token, payload);
  },
  async adminGatewayTopUp(tenantId: number, amount: number, token: string): Promise<{ credit_balance: number }> {
    return request('POST', `/admin/gateway/tenants/${tenantId}/credits`, token, { amount });
  },
  async adminGatewayReissueKey(tenantId: number, token: string): Promise<{ api_key: string }> {
    return request('POST', `/admin/gateway/tenants/${tenantId}/keys`, token);
  },
  async adminGatewayRevokeKey(keyId: number, token: string): Promise<{ ok: boolean }> {
    return request('POST', `/admin/gateway/keys/${keyId}/revoke`, token);
  },
  async adminGatewayJobs(
    token: string,
    opts: { tenantId?: number; limit?: number; offset?: number; engine?: string; status?: string; since?: string; until?: string; source?: string } = {},
  ): Promise<{ jobs: GatewayJob[]; total: number; limit: number; offset: number }> {
    const p = new URLSearchParams();
    p.set('limit', String(opts.limit ?? 50));
    p.set('offset', String(opts.offset ?? 0));
    if (opts.tenantId != null) p.set('tenant_id', String(opts.tenantId));
    if (opts.engine) p.set('engine', opts.engine);
    if (opts.status) p.set('status', opts.status);
    if (opts.source) p.set('source', opts.source);
    if (opts.since) p.set('since', opts.since);
    if (opts.until) p.set('until', opts.until);
    return request('GET', `/admin/gateway/jobs?${p.toString()}`, token);
  },
  async adminListUserTopics(userId: number, token: string): Promise<Topic[]> {
    return request<Topic[]>('GET', `/admin/users/${userId}/topics`, token);
  },

  // Admin creates a topic directly for a specified user (skips review, approved once persisted)
  async adminCreateTopicForUser(
    userId: number, payload: TopicPayload, token: string,
  ): Promise<Topic> {
    return request<Topic>('POST', `/admin/users/${userId}/topics`, token, payload);
  },

  // Admin fetches single run metadata
  async adminGetRun(runId: number, token: string): Promise<AdminRun> {
    return request<AdminRun>('GET', `/admin/runs/${runId}`, token);
  },

  // Admin cross-user batch-run overview
  async adminListRuns(
    token: string,
    opts: { day?: string; userId?: number; topicId?: number; status?: string; limit?: number; offset?: number } = {},
  ): Promise<AdminRun[]> {
    const params = new URLSearchParams();
    if (opts.day) params.set('day', opts.day);
    if (opts.userId !== undefined) params.set('user_id', String(opts.userId));
    if (opts.topicId !== undefined) params.set('topic_id', String(opts.topicId));
    if (opts.status) params.set('status', opts.status);
    if (opts.limit !== undefined) params.set('limit', String(opts.limit));
    if (opts.offset !== undefined) params.set('offset', String(opts.offset));
    const qs = params.toString();
    const suffix = qs ? `?${qs}` : '';
    return request<AdminRun[]>('GET', `/admin/runs${suffix}`, token);
  },

  async listTopicResponses(
    topicId: number, token: string,
    opts: { engine?: string; domain?: string; query?: string; period?: number; limit?: number } = {},
  ): Promise<ResponseRow[]> {
    const params = new URLSearchParams();
    if (opts.engine) params.set('engine', opts.engine);
    if (opts.domain) params.set('domain', opts.domain);
    if (opts.query) params.set('query', opts.query);
    if (opts.period) params.set('period', String(opts.period));
    if (opts.limit) params.set('limit', String(opts.limit));
    const qs = params.toString();
    const suffix = qs ? `?${qs}` : '';
    return request<ResponseRow[]>('GET', `/topics/${topicId}/responses${suffix}`, token);
  },
};

// ── mock helpers ────────────────────────────────────────────
function _mockMatrix(topicId: number): TrackingMatrix {
  const queries = ['Q1 跨境TMT投资律师', 'Q2 私募股权律师', 'Q3 资本市场律师'];
  const engines: EngineId[] = ['wenxin', 'doubao', 'yuanbao'];
  const now = new Date();
  const cells: QueryHitCell[] = [];
  for (const q of queries) {
    for (const e of engines) {
      const hit = q === queries[0] && (e === 'wenxin' || e === 'doubao');
      cells.push({
        query: q, engine: e,
        status: hit ? 'done' : 'done',
        first_hit_at: hit ? new Date(now.getTime() - 86400000).toISOString() : null,
        first_hit_response_id: hit ? 1 : null,
        last_checked_at: now.toISOString(),
        total_runs: 3,
        total_hits: hit ? 2 : 0,
      });
    }
  }
  return {
    topic_id: topicId,
    target: '<law-firm-a>', target_aliases: ['Example LLP', 'Example & Partners'],
    queries, engines,
    started_at: new Date(now.getTime() - 5 * 86400000).toISOString(),
    cells,
    timeline: engines.map(e => ({
      engine: e,
      first_hit_at: e === 'wenxin' || e === 'doubao'
        ? new Date(now.getTime() - 86400000).toISOString() : null,
      first_hit_query: e === 'wenxin' || e === 'doubao' ? queries[0] : null,
      days_after_start: e === 'wenxin' ? 3 : (e === 'doubao' ? 3 : null),
    })),
    total_runs: 3,
    total_cells: queries.length * engines.length,
    hit_cells: 2,
    hit_cells_pct: Math.round(2 / (queries.length * engines.length) * 1000) / 10,
  };
}

function _mockDrawer(_topicId: number, query: string, engine: EngineId): CellDrawer {
  return {
    cell: {
      query, engine, status: 'done',
      first_hit_at: new Date(Date.now() - 86400000).toISOString(),
      first_hit_response_id: 1, last_checked_at: new Date().toISOString(),
      total_runs: 3, total_hits: 2,
    },
    evidence: [
      {
        response_id: 1, run_id: 1, created_at: new Date(Date.now() - 86400000).toISOString(),
        engine, query, hit: true,
        hit_excerpt: '…推荐关注 [<law-firm-a>],在 TMT 行业有丰富积累…',
        source_url: 'https://yiyan.baidu.com/chat/abc',
        answer: 'mock answer body',
        citations: [{ url: 'https://gov.cn', domain: 'gov.cn', title: '示例' }],
      },
    ],
    insight: null,
  };
}

function _mockInsight(topicId: number, query: string, engine: EngineId): CellInsight {
  return {
    id: 1, topic_id: topicId, query, engine,
    window_start: new Date(Date.now() - 7 * 86400000).toISOString(),
    window_end: new Date().toISOString(),
    verdict: 'hit_unstable',
    summary: '近 7 天 3 次跑批命中 2 次,引擎倾向 listicle 答题套路',
    competitors_top3: [
      { name: '<law-firm-b>', count: 3, snippet: '…在跨境投资领域推荐<law-firm-b>…' },
      { name: '<law-firm-c>', count: 2, snippet: '…<law-firm-c>的 TMT 团队…' },
    ],
    recommendations: [
      { priority: 'P0', title: '加 TMT 案例长文', action: '30 天内 lawfirm-a.example 加 3 篇 TMT 海外并购深度案例', why: '该引擎对深度报告类内容权重高' },
      { priority: 'P1', title: '优化 PR 投放', action: '把"<law-firm-a> TMT 团队"投到 lawinfochina.com', why: '该域名在 citations 高频出现' },
    ],
    answer_format: 'listicle',
    citation_domains: ['gov.cn', 'lawinfochina.com'],
    evidence_response_ids: [1, 2, 3],
    llm_model: 'mock', prompt_version: 'cell_v1',
    generated_at: new Date().toISOString(),
    feedback: null,
  };
}

function _mockSoV(topicId: number, periodDays: number): ShareOfVoice {
  return {
    topic_id: topicId, target: 'Acme Cloud', period_days: periodDays,
    brand_count: 23,
    competitors_count_total: 41,
    saiv_pct: 35.9,
    competitors: [
      { name: '<competitor-a>', count: 18, pct: 28.1 },
      { name: '<competitor-b>', count: 12, pct: 18.8 },
      { name: '<competitor-c>', count: 6, pct: 9.4 },
      { name: '<competitor-d>', count: 3, pct: 4.7 },
      { name: '<competitor-e>', count: 2, pct: 3.1 },
    ],
    position_dist: { lead: 12, body: 8, tail: 3, unknown: 0 },
    optimal_rate_pct: 82.9,
    total_runs: 7,
    sample_size: 35,
  };
}

function _mockBriefing(topicId: number, id = 1): Briefing {
  return {
    id, topic_id: topicId,
    period_start: new Date(Date.now() - 7 * 86400000).toISOString(),
    period_end: new Date().toISOString(),
    body_md: '## 本周亮点\n- 文心一言 Q1 完成首次命中\n\n## 下周优先行动\n- 加 TMT 案例长文',
    kpi_snapshot: { hit_rate: 22.2, ttfm_days: 3, new_hits_count: 2, lost_hits_count: 0 },
    top_actions: [
      { priority: 'P0', title: 'TMT 案例库扩容', why: '文心引擎偏好深度报告', how: '30 天内加 3 篇' },
    ],
    delivered_email_at: null, feedback_score: null,
    llm_model: 'mock', prompt_version: 'briefing_v1',
    generated_at: new Date().toISOString(),
  };
}
