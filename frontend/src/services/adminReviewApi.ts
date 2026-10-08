// Admin review API — Phase C single-item review + Phase D whole-application review.
// All endpoints live under /api/admin/review, guarded by backend require_admin; callers must send an admin token.

import { localizedHeaders, readApiError } from './apiError';
import type { BrandProfile, SubmissionStatus, Topic } from './aiTelemetryApi';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

async function request<T>(
  method: string, path: string, token: string, body?: unknown,
  base: string = '/admin/review',
): Promise<T> {
  const init: RequestInit = {
    method,
    headers: localizedHeaders({
      Authorization: `Bearer ${token}`,
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    }),
  };
  if (body !== undefined) init.body = JSON.stringify(body);
  const resp = await fetch(`${API_BASE}${base}${path}`, init);
  if (!resp.ok) {
    const msg = await readApiError(resp, `Request ${method} ${path} failed`);
    throw new Error(msg);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json() as Promise<T>;
}

// ── Phase D — whole-application review ───────────────────────────

// The 5 states of project progress stage chips, value range aligned with backend admin_review.py::StageState.
export type StageState = 'done' | 'running' | 'pending' | 'blocked' | 'idle';

export interface TopicReviewListItem {
  topic_id: number;
  topic_name: string;
  user_id: number;
  user_email: string;
  submission_status: SubmissionStatus;
  submitted_at?: string | null;
  approved_at?: string | null;
  rejected_at?: string | null;
  profile_name: string;
  company_short_name: string;
  industry: string;
  seed_count: number;
  selected_query_count: number;
  version?: number;
  // Project progress stage 3-6 states; an un-upgraded backend returns all 'idle' (Pydantic default), compatibility unaffected
  diagnose_status: StageState;
  plan_status: StageState;
  content_status: StageState;
  insight_status: StageState;
}

export interface TopicChangelogEntry {
  at: string;
  actor_id?: number | null;
  actor_role: 'user' | 'admin' | 'system';
  field: string;
  before?: string | null;
  after?: string | null;
  note?: string | null;
  version?: number | null;
}

export interface ExpansionLogEntry {
  at: string;
  seed: string;
  model: string;
  expanded_count: number;
  raw_excerpt: string;
}

export interface TopicReviewDetail extends Topic {
  user_id: number;
  user_email: string;
  topic_changelog: TopicChangelogEntry[];
  expansion_log: ExpansionLogEntry[];
}

export interface AdminPatchTopicPayload {
  profile?: BrandProfile;
  seed_prompts?: string[];
  selected_query_texts?: string[];
  add_queries?: string[];
  note?: string;
}

export interface TopicProgressCell {
  query: string;
  engine: string;
  status: 'pending' | 'running' | 'done';
  hit?: boolean | null;
  last_checked_at?: string | null;
}

export interface PublishPlanItem {
  // Persisted fields (also sent back on PUT)
  id: string;
  seq: number;
  publish_date: string;
  // New seed-based flow: seed non-empty, query empty; legacy: seed null, query non-empty.
  seed?: string | null;
  query: string;
  // 2026-06-11 — multi-select monitored queries; body generation is asked to naturally cover this set of questions. Coexists with the single-value query; queries wins.
  queries?: string[];
  template_id?: number | null;
  platform?: string | null;
  // Which placement channel: '' undecided / aitoearn own authorized account / own owned site / rwj Ruanwenjiang paid placement.
  // platform is always the real platform name (Sohu/Zhihu), the channel is recorded separately -- whether a Ruanwenjiang resource ID is needed
  // depends on channel, not platform.
  channel?: string | null;
  // 2026-08-04 — additional placement platforms (one draft, multiple placements): the main platform is still platform (writing rules follow it),
  // each platform here gets its own placement task after approval.
  extra_platforms?: string[];
  // Preset placement outlet: on approval placement tasks are auto-created by (platform, media); Ruanwenjiang carries a resource ID and auto-pushes drafts
  media?: string | null;
  rwj_resource_id?: number | null;
  note?: string | null;
  // 2026-05-28 — per-row combo override (empty -> use profile default)
  creation_directions?: string[];
  copywriting_types?: string[];
  // Display fields aggregated live / computed via joins by the backend
  day: number;                          // Old field, = seq; kept for compatibility
  coverage_pct: number;
  priority: 'high' | 'med' | 'low';
  doc_id?: number | null;
  doc_status?: 'generating' | 'draft' | 'pending_review' | 'approved' | 'rejected' | 'published' | null;
  // Drafts that failed generation still have status pending_review -- looking only at doc_id/doc_status would misjudge them as drafted
  doc_failed?: boolean;
  doc_error?: string | null;
  template_name?: string | null;
  template_kind?: string | null;
  suggested_platforms: string[];
  // Scheduling rationale (machine-written, read-only): why this row's platform/template/preferences/question/seed/outlet each are
  // the way they are. An item manually changed in the editor is downgraded to {reason:'manual'}.
  why?: PlanRowWhy | null;
  // How this row came about: strategy plan tier N / missed-question backflow / candidate topic insert / manual
  origin?: { type?: string; tier?: string; auto?: boolean;
             [k: string]: unknown } | null;
}

export interface PlanItemDraft {
  id?: string | null;                   // New rows may omit id; the backend fills it
  seq: number;
  publish_date: string;
  seed?: string | null;                 // Non-empty for seed-based new rows; empty for legacy (one or the other)
  query: string;                        // Legacy rows ∈ monitored; seed-based rows allow ""
  queries?: string[];                   // 2026-06-11 — multi-select monitored queries, each must be ∈ monitored
  template_id: number;
  platform: string;
  // Which placement channel: '' undecided / aitoearn own authorized account / own owned site / rwj Ruanwenjiang paid placement.
  // platform is always the real platform name (Sohu/Zhihu), the channel is recorded separately -- whether a Ruanwenjiang resource ID is needed
  // depends on channel, not platform.
  channel?: string | null;
  // 2026-08-04 — additional placement platforms (one draft, multiple placements); the backend creates one placement task per platform
  extra_platforms?: string[];
  media?: string | null;
  rwj_resource_id?: number | null;
  note?: string | null;
  // 2026-05-28 — per-row combo override (empty -> use profile default)
  creation_directions?: string[];
  copywriting_types?: string[];
}

// New flow: draft (ops editing the publish table) / confirmed (confirmed, articles running) / failed
// Compatible old values: ready / generating (being gradually replaced by the backend)
export type ExecutionPlanStatus = 'draft' | 'confirmed' | 'failed' | 'ready' | 'generating';

export interface ExecutionPlan {
  id: number;
  topic_id: number;
  generated_at: string;
  generated_by_reviewer_id?: number | null;
  status: ExecutionPlanStatus;
  error?: string | null;
  overview: Record<string, unknown>;
  topic_changelog: TopicChangelogEntry[];
  expansion_log: ExpansionLogEntry[];
  monitored_queries: string[];
  // 2026-08-14 — seed prompt -> prompts under that seed (already intersected with monitored_queries).
  // The row editor groups and collapses query selection by it; the empty-string key = legacy queries not belonging to any seed.
  // Optional: when an old backend lacks this field, the frontend falls back to a flat list by monitored_queries.
  seed_queries?: Record<string, string[]>;
  run_id?: number | null;
  run_status?: 'running' | 'success' | 'failed' | null;
  progress: TopicProgressCell[];
  progress_done: number;
  progress_total: number;
  publishing_plan: PublishPlanItem[];
  // Why the publish table is still empty (only with a start config):
  //   waiting_run — the baseline is still running; placement platforms are set from measured sources and the table laid out once it's done
  //   no_seeds    — finished but no seeds/templates, so no plan can be scheduled
  plan_pending_reason?: 'waiting_run' | 'no_seeds' | null;
  confirmed_at?: string | null;
  confirmed_by_id?: number | null;
  last_edited_at?: string | null;
}

// ── Start config / source analysis ───────────────────────────

// Scheduling metric definition set when starting the project. Placement platforms aren't chosen here -- computed from measured sources once the baseline finishes.
export interface StartTopicOptions {
  months: number;                       // How many months to lay out at once (1-3)
  source: 'seeds' | 'strategy';
  per_day: number;                      // Articles per day (1-10)
  template_ids: number[];
  coverage_queries: string[];           // Covered queries: picked from prompts; empty = all
  platform_mode?: 'sources' | 'template';
}

export interface SourceDomainRow {
  domain: string;
  count: number;
  source_type?: string;
  engines?: string[];
  sample_titles?: string[];
  platform?: string | null;             // Mapped placeable channel
  // What can be done with this domain: platform = placement via the platform / rwj_site = Ruanwenjiang paid submission /
  // profile = can't place but can update the company profile yourself (Tianyancha, encyclopedias) / none = reference only
  action?: 'platform' | 'rwj_site' | 'profile' | 'none';
  media?: { resource_id: number; title: string; price_yuan: number;
            publish_rate?: number | null; res_type: string }[];
}

export interface QuerySourceRow {
  query: string;
  responses: number;
  hit_pct: number;
  engines: string[];
  citations: number;
  own_cited: number;
  top_domains: { domain: string; count: number; platform?: string | null }[];
}

export interface PlatformQuota {
  slots: string[];
  // Measured cited channel ranking (placeability not filtered) -- the raw conclusion of "who did the batch run actually cite"
  measured?: {
    platform: string; citations: number; share_pct: number; investable: boolean;
    // free = publish directly via own account/own site; paid = only via Ruanwenjiang paid placement; none = can't place
    investable_kind?: 'free' | 'paid' | 'none';
    unit_price?: number | null;        // Reference unit price for paid channels (yuan, resource library median)
    resource_count?: number | null;    // Number of placeable resources on this platform within the price cap
    // Recommended outlets: the accounts the schedule will actually bind (same source as the publish table, not a separate recommendation)
    media?: {
      resource_id: number; title: string; res_type: string;
      price_yuan: number; publish_rate?: number | null;
    }[];
    domains: { domain: string; count: number }[];
  }[];
  paid_only?: string[];
  paid_max_share?: number;
  cost?: {
    per_day_yuan: number; per_month_yuan: number;
    paid_slots: number; free_slots: number;
    basis: string; disclaimer: string;
  };
  breakdown: {
    platform: string; citations: number; share_pct: number; slots: number;
    paid?: boolean; unit_price?: number;
    domains: { domain: string; count: number }[];
  }[];
  unavailable: { platform: string; citations: number }[];
  // Directory / encyclopedia sites where you can't place but can update your own profile
  profile_sites?: { domain: string; count: number; source_type?: string }[];
  unmapped: { domain: string; count: number; source_type?: string }[];
  available: string[];
  notes: string[];
  // Structured copy of notes (with code/level), rendered as cards by type on the frontend. Plain-text notes are kept
  notes_struct?: { code: string; text: string; level: 'info' | 'warn' }[];
  // Channels dropped by the long-tail threshold -- answers "Xiaohongshu was clearly cited, why wasn't a single article scheduled"
  excluded?: { platform: string; citations: number; share_pct: number;
               reason: string; threshold: number }[];
  // How many articles the paid cap moved from whom to whom
  paid_cap_moves?: { from: string; to: string; slots: number }[];
  // Threshold snapshot (so old plans' rationale still makes sense after constants change)
  thresholds?: { min_share_pct: number; paid_max_share: number;
                 cold_start_order: string[] };
  responses_total?: number;
  cold_start: boolean;
}

/** Plan-level scheduling rationale snapshot -- stored in plan.overview.plan_basis, it is "why it was laid out this way back then" */
export interface PlanBasis {
  v: number;
  built_at?: string;
  trigger?: 'start' | 'generate_items' | 'refill' | 'lazy_seed' | string;
  actor_id?: number | null;
  options?: Record<string, unknown>;
  seed_source?: { kind?: string; seed_count?: number; tiers?: string[] };
  templates?: { id: number; name: string; kind: string; platforms: string[] }[];
  /** null = this layout didn't use source quotas (laid out by the templates' own platforms) */
  quota?: PlatformQuota | null;
  /** Placement strategy used for this plan (trimmed); null = this layout didn't follow a strategy */
  strategy?: PlacementStrategy | null;
  history?: { built_at?: string; trigger?: string; headline?: string }[];
}

// ── Placement strategy (the plan's "Placement strategy" tab) ───────────
// Deterministically generated by backend services/placement_strategy.py: knowledge base (which sources each of the five major engines trusts)
// + the topic's measured citation data -> engine priority / per-engine placement checklist / content format / placement cadence.

export interface StrategyEnginePriority {
  engine: string;
  name: string;
  rank: number;
  /** 0-1; quota weighting is based on it (1 = the engine most worth placing for) */
  weight: number;
  score?: number;
  share_pct: number;
  hit_rate_pct: number;
  citations: number;
  ecosystem: string;
  exclusive: string;
  summary: string;
  /** true = no engine-specific source definition compiled yet for this engine; generic advice given */
  generic?: boolean;
  industry_weight?: number;
  rationale: string;
}

export interface StrategyChecklistItem {
  key: string;
  label: string;
  tier: string;
  note?: string;
  /** Only for the kind that can go into the publish table */
  platform?: string;
  investable?: boolean;
  paid?: boolean;
  /** profile = build a profile / account = create an account (these two kinds go to the optimization task center) */
  kind?: string;
  /** Already occupying this channel in measurements */
  occupied?: boolean;
}

export interface StrategyPlatformPlan {
  engine: string;
  name: string;
  rank: number;
  priority_list: string[];
  publishable: StrategyChecklistItem[];
  infra: StrategyChecklistItem[];
}

export interface PlacementStrategy {
  v: number;
  kb_version: string;
  computed_at?: string;
  target?: string;
  per_day?: number;
  months?: number;
  industry: { key: string; name: string; core: string; matched: boolean;
              compliance?: string };
  diagnosis: {
    responses_total: number;
    own_cited_responses: number;
    engines: { engine: string; name: string; responses: number;
               hit_rate_pct: number; citations: number;
               top_domains?: { domain: string; count: number }[];
               answer_formats?: Record<string, number>; measured: boolean }[];
  };
  engine_priority: StrategyEnginePriority[];
  platform_plan: StrategyPlatformPlan[];
  gaps: { engine: string; name: string; missing: string[]; blocked: string[];
          infra_missing: string[] }[];
  content_forms: { engine: string; name: string; rank: number; forms: string[];
                   avoid: string; recency: string;
                   observed_formats: { format: string; count: number }[] }[];
  cadence: { key: string; weeks: string; focus: string; planned_docs: number;
             items: { engine: string; label: string; platform: string }[] }[];
  cold_start_order: string[];
  notes: { code: string; text: string; level: 'info' | 'warn' }[];
}

export interface PlacementStrategyOut {
  topic_id: number;
  strategy: PlacementStrategy;
  /** Always true: computed from current data, not the copy used when laying out this plan */
  is_live?: boolean;
  computed_at?: string | null;
}

/** Row-level scheduling rationale -- see backend/geo/services/plan_basis.py for each sub-block's reason */
export interface PlanRowWhy {
  v?: number;
  platform?: { reason: string; share_pct?: number; citations?: number;
               day_slots?: number; rank?: number;
               prev_reason?: string | null; prev_value?: unknown };
  template?: { reason: string; matched_platform?: string; wanted_platform?: string;
               prev_reason?: string | null; prev_value?: unknown };
  combo?: { reason: string; kind?: string };
  queries?: { reason: string; count?: number; day_total?: number; per_day?: number;
              prev_reason?: string | null; prev_value?: unknown };
  seed?: { reason: string; tier?: string;
           prev_reason?: string | null; prev_value?: unknown };
  media?: { reason: string; rank?: number; pool?: number;
            publish_rate?: number | null; price_yuan?: number | null;
            prev_reason?: string | null; prev_value?: unknown };
}

export interface SourceInsights {
  topic_id: number;
  responses_total: number;
  own_cited_responses: number;
  top_sources: SourceDomainRow[];
  source_type_share: { source_type: string; count: number; pct: number }[];
  per_engine: { engine: string; responses: number; hit_rate_pct: number;
                top_domains: { domain: string; count: number }[] }[];
  per_query: QuerySourceRow[];
  quota: PlatformQuota;
  /** Always true: this data is **computed from current data**, not what was seen when laying out this plan */
  is_live?: boolean;
  computed_at?: string | null;
}

// ── v3.3 strategy plan ───────────────────────────

export type SolutionStatus = 'idle' | 'generating' | 'ready' | 'failed';
export type SolutionSeverity = 'high' | 'med' | 'low';

export interface SolutionDiagnosisCheck {
  category: string;
  status: 'PASS' | 'WARN' | 'FAIL' | 'INFO' | string;
  message: string;
  fix?: string | null;
}

export interface SolutionDiagnosisCluster {
  key: string;
  title_zh: string;
  severity: SolutionSeverity;
  summary: string;
  bullets: string[];
  checks: SolutionDiagnosisCheck[];
}

export interface SolutionExecutionLayer {
  key: string;
  title_zh: string;
  hint: string;
  clusters: string[];
}

export interface SolutionDiagnosis {
  url: string;
  score: number;
  grade: string;
  pass_count: number;
  warn_count: number;
  fail_count: number;
  info_count: number;
  clusters: SolutionDiagnosisCluster[];
  execution_layers: SolutionExecutionLayer[];
  all_checks: SolutionDiagnosisCheck[];
}

export interface SolutionSevenStep {
  step: number;
  name: string;
  core_goal: string;
  core_action: string;
  output_value: string;
}

export interface SolutionKeywordTier {
  tier: string;
  title_zh: string;
  description: string;
  keywords: string[];
}

export interface SolutionVisionItem {
  title: string;
  body: string;
}

export interface SolutionQueryItem {
  text: string;
  cluster_id: number;       // -1 means ungrouped
  seed: string;             // Which seed prompt this query was expanded from at the time (empty string for legacy)
}

export interface SolutionQueryCluster {
  cluster_id: number;
  label: string;
}

export interface SolutionQueriesSnapshot {
  clusters: SolutionQueryCluster[];
  queries: SolutionQueryItem[];
}

export interface TopicStrategicSolution {
  id: number;
  topic_id: number;
  status: SolutionStatus;
  website_url: string;
  error?: string | null;
  generated_by_admin_id?: number | null;
  llm_model: string;
  created_at?: string | null;
  updated_at?: string | null;
  brand_snapshot?: BrandProfile | null;
  diagnosis?: SolutionDiagnosis | null;
  seven_steps: SolutionSevenStep[];
  keyword_tiers: SolutionKeywordTier[];
  vision: SolutionVisionItem[];
  queries_snapshot?: SolutionQueriesSnapshot | null;
}

// ── Flywheel P1-B: topic backflow (candidate topics -> one-click convert to plan items) ───────────
export interface PlanCandidateUnhit { query: string; seed: string }
export interface PlanCandidateInsight {
  query: string; engine: string; verdict: string;
  priority: string; title: string; action: string; why: string;
}
export interface PlanCandidates {
  topic_id: number;
  unhit_queries: PlanCandidateUnhit[];
  insight_recommendations: PlanCandidateInsight[];
}
export interface BatchGenResult {
  queued: number;
  matched: number;          // Rows matching the scope (including skipped)
  skipped_existing: number; // Skipped because already drafted (when "also regenerate drafted" isn't checked)
  // Rows blocked because already published although "also regenerate drafted" was checked -- overwriting a published draft would make live content
  // diverge from the DB, so the backend blocks it uniformly; rewriting is only possible via single-item regeneration
  skipped_published?: number;
}

export interface InsertPlanItemReq {
  seed?: string; query?: string; queries?: string[];
  note?: string | null;
  origin?: { type: string; ref?: unknown };
}

export const adminReviewApi = {
  async getPlanCandidates(topicId: number, token: string): Promise<PlanCandidates> {
    return request<PlanCandidates>(
      'GET', `/${topicId}/plan/candidates`, token, undefined, '/admin/topics');
  },
  async insertPlanItems(
    topicId: number, items: InsertPlanItemReq[], token: string,
  ): Promise<ExecutionPlan> {
    return request<ExecutionPlan>(
      'POST', `/${topicId}/plan/insert-items`, token, { items }, '/admin/topics');
  },

  // Project list / details / edit (admin view)
  async listTopicReviews(token: string, status?: SubmissionStatus): Promise<TopicReviewListItem[]> {
    const qs = status ? `?status=${status}` : '';
    return request<TopicReviewListItem[]>('GET', `/topics${qs}`, token);
  },
  // User self-service version: returns project progress only for the current user's own topics (used by clients after the workbench opened externally).
  // Uses /api/topics/mine/reviews (non-admin channel, base='').
  async listMyTopicReviews(
    token: string, status?: SubmissionStatus, enterpriseUserId?: number | null,
  ): Promise<TopicReviewListItem[]> {
    const params = new URLSearchParams();
    if (status) params.set('status', status);
    if (enterpriseUserId) params.set('enterprise_user_id', String(enterpriseUserId));
    const qs = params.size ? `?${params.toString()}` : '';
    return request<TopicReviewListItem[]>('GET', `/topics/mine/reviews${qs}`, token, undefined, '');
  },
  async getTopicReview(topicId: number, token: string): Promise<TopicReviewDetail> {
    return request<TopicReviewDetail>('GET', `/topic/${topicId}`, token);
  },
  async patchTopic(topicId: number, payload: AdminPatchTopicPayload, token: string): Promise<TopicReviewDetail> {
    return request<TopicReviewDetail>('PATCH', `/topic/${topicId}`, token, payload);
  },
  async rerunTopic(topicId: number, token: string): Promise<ExecutionPlan> {
    return request<ExecutionPlan>('POST', `/topic/${topicId}/rerun`, token);
  },
  // New channel without review: start project / restart.
  // Idempotent by default (returns directly if the topic already has a ready/generating plan); force=true forces a rerun.
  // With options, the backend only records the scheduling metric definition, and the publish table is laid out from measured sources once the baseline finishes (not generated immediately).
  async startTopic(
    topicId: number, token: string, force = false, options?: StartTopicOptions,
  ): Promise<ExecutionPlan> {
    const qs = force ? '?force=true' : '';
    return request<ExecutionPlan>(
      'POST', `/${topicId}/start${qs}`, token, options, '/admin/topics',
    );
  },
  async getExecutionPlan(topicId: number, token: string): Promise<ExecutionPlan> {
    return request<ExecutionPlan>('GET', `/topic/${topicId}/execution-plan`, token);
  },
  // Source analysis (plan tab): measured cited domain ranking / sources per prompt / platform quota preview.
  // per_day only affects the number of slots in the quota preview; not persisted to DB.
  async getSourceInsights(
    topicId: number, token: string, perDay = 5,
  ): Promise<SourceInsights> {
    return request<SourceInsights>(
      'GET', `/topic/${topicId}/source-insights?per_day=${perDay}`, token);
  },
  // Placement strategy (computed live, not persisted to DB) -- perDay / months only affect article-count conversion in the cadence table
  async getPlacementStrategy(
    topicId: number, token: string, perDay = 5, months = 1,
  ): Promise<PlacementStrategyOut> {
    return request<PlacementStrategyOut>(
      'GET',
      `/topic/${topicId}/placement-strategy?per_day=${perDay}&months=${months}`,
      token);
  },
  // Regenerate the plan snapshot (materials/prompts/logs rebuilt from the topic's current state; publish table and status untouched)
  async regenerateExecutionPlan(topicId: number, token: string): Promise<ExecutionPlan> {
    return request<ExecutionPlan>(
      'POST', `/topic/${topicId}/execution-plan/regenerate`, token,
    );
  },
  // Replace the publish table wholesale (allowed in both draft / confirmed)
  async updateExecutionPlan(
    topicId: number, items: PlanItemDraft[], token: string,
  ): Promise<ExecutionPlan> {
    return request<ExecutionPlan>(
      'PUT', `/topic/${topicId}/execution-plan`, token, { items },
    );
  },
  // Rebuild the publish table from generation options (whole-table replacement): articles per day / template rotation / seed or strategy plan source
  async generatePlanItems(
    topicId: number,
    payload: {
      source: 'seeds' | 'strategy'; per_day: number; template_ids: number[];
      // sources = placement platforms allocated by measured cited-source share;
      // strategy = produce the placement strategy first, then allocate by the citation distribution weighted by engine priority;
      // template = keep the template defaults (old behavior)
      platform_mode?: 'sources' | 'strategy' | 'template'; months?: number;
    },
    token: string,
  ): Promise<ExecutionPlan> {
    return request<ExecutionPlan>(
      'POST', `/topic/${topicId}/execution-plan/generate-items`, token, payload,
    );
  },
  // Add one plan row on a given date (id/seq decided by the backend); with generate_now=true dispatch generation for that single item immediately
  async addPlanItem(
    topicId: number,
    payload: { item: PlanItemDraft; generate_now: boolean },
    token: string,
  ): Promise<ExecutionPlan> {
    return request<ExecutionPlan>(
      'POST', `/topic/${topicId}/execution-plan/items`, token, payload,
    );
  },
  // One-off batch article generation (by default only fills rows without drafts; days = window of the next N days, limit = first N rows)
  // item_ids: plan rows named for generation (used by the calendar page's "generate / replan and regenerate this day"); when given, days/limit are ignored
  async batchGenerateDocs(
    topicId: number,
    payload: {
      item_ids?: string[]; days?: number | null; limit?: number | null;
      include_existing?: boolean;
    },
    token: string,
  ): Promise<BatchGenResult> {
    return request<BatchGenResult>(
      'POST', `/topic/${topicId}/execution-plan/generate-docs`, token, payload,
    );
  },
  // Confirm -> trigger article generation (draft -> confirmed; when confirmed it's an idempotent increment, force=true regenerates everything)
  async confirmExecutionPlan(
    topicId: number, token: string, force = false,
  ): Promise<ExecutionPlan> {
    const qs = force ? '?force=true' : '';
    return request<ExecutionPlan>(
      'POST', `/topic/${topicId}/execution-plan/confirm${qs}`, token,
    );
  },
  // Single-item regeneration (overwrites the body of an existing doc)
  async regeneratePlanItem(
    topicId: number, itemId: string, token: string,
  ): Promise<ExecutionPlan> {
    return request<ExecutionPlan>(
      'POST',
      `/topic/${topicId}/execution-plan/items/${encodeURIComponent(itemId)}/regenerate`,
      token,
    );
  },

  // ── v3.3 strategy plan ─────────────────────────
  async getStrategicSolution(topicId: number, token: string): Promise<TopicStrategicSolution> {
    // When the backend has no record it returns a stub with status='idle', so no null branch is needed here
    return request<TopicStrategicSolution>('GET', `/topic/${topicId}/solution`, token);
  },
  async generateStrategicSolution(
    topicId: number, websiteUrl: string, token: string,
  ): Promise<TopicStrategicSolution> {
    return request<TopicStrategicSolution>(
      'POST', `/topic/${topicId}/solution/generate`, token,
      { website_url: websiteUrl },
    );
  },
};
