// Phase D.1 — user-side content API.
// The backend is mounted at /api/content/*, with owner-only checks.

import { localizedHeaders, throwApiError } from './apiError';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

export type DocStatus = 'generating' | 'draft' | 'pending_review' | 'approved' | 'rejected' | 'published';
export type DocSource = 'ai' | 'user';

export interface PublishTarget {
  platform: string;
  media: string;
  url?: string;
  marked_at?: string;
  marked_by?: number;
  // The real author name recorded per platform when importing the team placement ledger (source='user'), which is entirely different from marked_by (the user id
  // of the admin who performed the publish); don't mix them up.
  author?: string;
}

export interface ContentDoc {
  id: number;
  topic_id: number;
  execution_plan_id?: number | null;
  created_at: string;
  // Last write time. Regeneration overwrites in place (created_at unchanged), so to know when the body was
  // written you can only look at this; existing drafts were backfilled with created_at by migration, so it's never empty.
  updated_at?: string | null;
  source_query_text: string;
  title: string;
  body_markdown: string;
  summary: string;
  llm_model: string;
  generation_error?: string | null;
  /** Number of automatic retries so far (reset to zero on success) */
  gen_attempts?: number;
  /** Next automatic retry time; empty = no more retries (terminal error / cap reached / manually abandoned) */
  gen_next_retry_at?: string | null;
  status: DocStatus;
  selected_for_review: boolean;
  review_decision_at?: string | null;
  reviewer_id?: number | null;
  reject_reason?: string | null;
  publish_targets: PublishTarget[];
  source: DocSource;
  // Template used for generation (content_templates); null for user-supplied drafts / legacy without a template
  template_id?: number | null;
  template_name?: string | null;
  template_kind?: string | null;         // long-form / short-form / FAQ / case study ...
  // 2026-07-31 — back-link to the publish plan row: which plan row produced this draft (empty for user-supplied drafts)
  plan_item_id?: string | null;
  plan_seq?: number | null;              // #N in the plan table (from 1)
  plan_publish_date?: string | null;     // Planned publish date (YYYY-MM-DD or with THH:MM)
  // 2026-08-05 — linked info: which seed prompt it was written from, which prompts it should cover, which platform it targets
  plan_seed?: string | null;
  plan_queries?: string[];
  plan_platform?: string | null;
  // URL-level ROI hits: {engine: [response_id]} — an empty object means never cited by AI
  cited_by: Record<string, number[]>;
}

export interface ContentStats {
  draft: number;
  pending_review: number;
  approved: number;
  rejected: number;
  published: number;
  total: number;
  ai_count: number;
  user_count: number;
  /** Number of drafts that failed generation. They're also pending_review in the DB; the backend counts them separately by generation_error
   *  and subtracts them from pending_review -- the body was never generated, so they shouldn't be mixed into "pending review". */
  gen_failed: number;
}

export interface UserDocSubmitPayload {
  title: string;
  body_markdown: string;
  summary?: string;
  source_query_text?: string;
  submit_for_review?: boolean;
}

export interface UserDocUpdatePayload {
  title?: string;
  body_markdown?: string;
  summary?: string;
  source_query_text?: string;
}

// ── Publish tasks / URL backfill (flywheel P1) ──────────────────────────────
export type PublishTaskStatus =
  | 'planned' | 'pending_publish' | 'publishing' | 'pending_url'
  | 'draft_saved' | 'url_submitted' | 'published' | 'failed' | 'cancelled';

export interface PublishTask {
  id: number;
  topic_id: number;
  doc_id: number;
  vscl_action_id?: number | null;
  platform: string;
  media_name: string;
  channel_type: 'auto_account' | 'manual_account' | 'external_media';
  publish_mode: 'auto' | 'manual' | 'paid_media';
  status: PublishTaskStatus;
  planned_publish_at?: string | null;
  actual_published_at?: string | null;
  published_url?: string | null;
  proof?: {
    note?: string;
    order_no?: string;
    screenshot_url?: string;
    platform_code?: string;
    external_post_id?: string;
    verification_method?: 'url' | 'platform_api';
    verification_executor?: 'plugin' | 'server_pool';
  } | null;
  // Paid media channel (Ruanwenjiang) extensions: resource snapshot / draft ID / quote
  channel_meta?: {
    channel?: string;
    type?: string;
    resource_id?: number;
    resource_title?: string;
    draft_id?: number | null;
    quoted_price?: string;
    order_no?: string;
    // Images not carried over in the body when pushing the draft / ordering (file not on this host, format not accepted by the platform, upload failed)
    dropped_images?: { url: string; name: string; reason: string }[];
    // AI automatic outlet selection (media_select)
    auto_selected?: boolean;
    select_source?: 'attribution' | 'llm' | 'rules';
    select_reason?: string;
    // aitoearn multi-platform distribution channel
    account_id?: string;
    account_type?: string;
    flow_id?: string;
    platform_work_id?: string;
    publish_warnings?: string[];
    candidate_url?: string;
    url_verification?: {
      candidate_url?: string;
      status?: 'pending' | 'valid' | 'invalid' | 'blocked' | 'verified' | string;
      attempts?: number;
      next_at?: string | null;
      last_at?: string | null;
      last_error?: string | null;
      source?: string | null;
      verification_method?: 'url' | 'platform_api';
      verification_executor?: 'plugin' | 'server_pool';
      platform_code?: string;
      external_post_id?: string;
      raw_status?: number;
      publish_time?: string | null;
      audit_fail_reason?: string | null;
      initial_delay_seconds?: number;
      confirmation_delay_seconds?: number;
      first_published_at?: string | null;
      last_observed_at?: string | null;
      last_platform_status?: string | null;
    };
  } | null;
  /** The wholesale price we pay upstream -- only staff get it */
  cost_cents?: number | null;
  /** Platform name shown to users for paid tasks. For regular users `platform` itself is already replaced with this value --
   *  not a single character of the vendor's Chinese name is sent, so determine the type with is_paid_media rather than by platform name. */
  platform_display?: string | null;
  is_paid_media?: boolean;
  /** External quote (yuan) */
  retail_price_yuan?: string | null;
  /** How much this task actually charged the user (net, in cents) */
  charged_cents?: number | null;
  last_error?: string | null;
  created_at?: string | null;
}

export interface PublishTargetIn {
  platform: string;
  media_name?: string;
  channel_type?: string;
}

export interface SubmitUrlPayload {
  url: string;
  actual_published_at?: string;
  proof?: {
    note?: string;
    order_no?: string;
    screenshot_url?: string;
    platform_code?: string;
    external_post_id?: string;
    verification_method?: 'url' | 'platform_api';
    verification_executor?: 'plugin' | 'server_pool';
  };
  verify_before_publish?: boolean;
}

export interface PublishVerificationReport {
  platform_code: 'ctrip';
  external_post_id: string;
  status: 'reviewing' | 'published' | 'rejected' | 'draft' | 'not_found' | 'query_error';
  raw_status?: number;
  audit_fail_reason?: string;
  publish_time?: string;
  observation_id?: string;
  observed_at?: string;
  error_message?: string;
}

export interface AutoGenerateConfig {
  enabled: boolean;
  time: string;       // "HH:MM"
  count: number;
  publish_enabled?: boolean;   // Auto-publish by publish_date toggle; if omitted the backend leaves it unchanged
  approve_enabled?: boolean;   // Auto-approve drafts; if omitted the backend leaves it unchanged
  publish_paid_enabled?: boolean;  // Auto-order paid media on the scheduled date (spends money); if omitted the backend leaves it unchanged
  // What time date-only rows publish that day, "HH:MM"; if omitted the backend leaves it unchanged.
  // Only affects plan rows without a time -- rows written as YYYY-MM-DDTHH:MM in the publish plan follow their own, unaffected.
  publish_time?: string;
}

export interface AutoGenerateState extends AutoGenerateConfig {
  auto_generate_last_run_at?: string | null;
  auto_publish_enabled?: boolean;
  auto_approve_enabled?: boolean;
  auto_publish_paid_enabled?: boolean;
  auto_publish_time?: string;
}

async function requestAt<T>(
  prefix: string, method: string, path: string, token: string, body?: unknown,
): Promise<T> {
  const init: RequestInit = {
    method,
    headers: localizedHeaders({
      Authorization: `Bearer ${token}`,
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    }),
  };
  if (body !== undefined) init.body = JSON.stringify(body);
  const resp = await fetch(`${API_BASE}${prefix}${path}`, init);
  // Throw BackendError rather than Error -- cases like insufficient balance need different landing actions by business code
  // (INSUFFICIENT_BALANCE should offer "Top up"; with just a message the frontend would have to guess from the text)
  if (!resp.ok) await throwApiError(resp, `Request ${method} ${path} failed`);
  if (resp.status === 204) return undefined as T;
  return resp.json() as Promise<T>;
}

async function request<T>(
  method: string, path: string, token: string, body?: unknown,
): Promise<T> {
  return requestAt('/content', method, path, token, body);
}

// ── Ruanwenjiang paid media channel (staff-only, backend /api/rwj/*) ──────────────
export interface RwjStatus {
  provider?: string;      // rwj = Ruanwenjiang / mjk = Meijieku
  label?: string;         // Localized site name (the vendor's full name isn't shown directly in the UI, see publishShared)
  configured: boolean;
  level: number | null;
  balance: string | null;
  error?: string;         // Reason when this site alone is unavailable; doesn't affect the other vendor
}

export interface RwjProvidersResult {
  default: string;
  providers: RwjStatus[];
}

export interface RwjMediaRow {
  resource_id: number;
  title: string;
  display_price: string;
  delivery: { label?: string; value?: number | null };
  delivery_options: { label?: string; value?: number | null }[];
  publish_rate?: string | number | null;
  publish_time?: string | null;
  pc_weigh?: string | number | null;
  wap_weigh?: string | number | null;
  remarks?: string | null;
  channel?: string | null;   // Channel type (news/IT tech...)
  region?: string | null;    // Region
  include?: string | null;   // Indexing status
}

export interface RwjFilterDim {
  field: string;                                  // field_1 …
  label: string;                                  // Channel type/portal site/region...
  options: { id: number; title: string }[];
}

export interface RwjMediaFilters {
  type: string;
  provider?: string;
  dims: RwjFilterDim[];
}

export type RwjResourceType = 'Website' | 'Wemedia' | 'Shortvideo' | 'Question' | 'Ghostwrite';
export type RwjSort = 'price_asc' | 'price_desc' | 'rate_desc' | 'weigh_desc';

export interface RwjMediaSearchResult {
  provider?: string;
  label?: string;
  level: number;
  total: number;
  page: number;
  last_page: number;
  orderable: boolean;   // Whether this type supports online ordering (currently website media/self-media)
  rows: RwjMediaRow[];
}

// Price check result. provider / resource_id / price (wholesale price) are only available to staff --
// for regular users these keys don't exist in the response at all, so they're all optional.
export interface RwjPublishPreview {
  task_id: number;
  provider?: string;
  provider_label?: string;
  resource_id?: number;
  resource_title?: string;
  media_name?: string | null;
  delivery: { label?: string; value?: number | null };
  price?: string;
  quoted_price?: string | null;
  price_changed: boolean;
  /** External quote -- used for both display and the expected order price */
  retail_price: string | null;
  retail_price_cents: number | null;
  balance?: string | null;
  publish_enabled: boolean;
  price_tolerance_pct: number;
}

// Placing with multiple outlets at once: result of pushing drafts (one task per outlet; those failing the price check listed separately)
export interface RwjPushDraftResult {
  tasks: PublishTask[];
  failed: { resource_id: number; resource_title: string; message: string }[];
  dropped_images?: { url: string; name: string; reason: string }[];
  task: PublishTask | null;
}

export interface RwjBatchPreviewItem {
  task_id: number;
  doc_id?: number;
  provider?: string;
  provider_label?: string;
  type?: string;
  resource_id?: number;
  resource_title?: string;
  media_name?: string | null;
  delivery: { label?: string; value?: number | null };
  price?: string;
  quoted_price?: string | null;
  price_changed: boolean;
  retail_price: string | null;
  retail_price_cents: number | null;
}

export interface RwjTaskError {
  task_id?: number;
  media_name?: string | null;
  code: string;
  message: string;
  fatal?: boolean;
}

// Batch price check: total price + one confirmation token covering all outlets
export interface RwjBatchPreview {
  items: RwjBatchPreviewItem[];
  failed: RwjTaskError[];
  count: number;
  total_retail_cents: number;
  total_retail_price: string;
  price_changed_count: number;
  /** Total upstream cost and supplier balance, only available to staff */
  total_price?: string;
  balances?: Record<string, string | null> | null;
  publish_enabled: boolean;
  price_tolerance_pct: number;
}

// Batch order result: succeeded / failed (per-item reasons) / not executed after abort
export interface RwjBatchPublishResult {
  succeeded: PublishTask[];
  failed: RwjTaskError[];
  skipped: { task_id: number; media_name?: string | null }[];
  aborted: boolean;
  /** How much this batch actually deducted from the user's balance (cents) */
  total_charged_cents: number;
}

// ── aitoearn multi-platform distribution channel (logged-in users' accounts are isolated by owner in the backend) ──────
export interface AitoearnStatus {
  configured: boolean;
  account_count: number | null;
  auth_platforms?: string[];
  error?: string;
}

export interface AitoearnAccount {
  account_id: string;
  account_type: string;      // twitter / facebook / instagram …
  platform: string;          // Display name (X/Facebook/Instagram...)
  nickname: string;
  avatar?: string | null;
  status?: number | null;
  fans_count?: number | null;
  max_title?: number | null;
  max_body?: number | null;
  max_images?: number | null;
  need_cover: boolean;
  render: 'html' | 'plain';
  option_schema?: unknown;
  binding_id?: number;
  scope?: 'personal' | 'company';
  publish_enabled?: boolean;
}

export interface AitoearnAuthStart {
  attempt_id: string;
  url: string;
  expires_at: string;
  auth_instructions?: Record<string, string>;
}

// Direct publishing via plugin (currently Xiaohongshu only): after the backend creates the task, the browser extension takes over publishing
export interface AitoearnPluginStartResult {
  task: PublishTask;
  action: 'ready' | 'skip';
  title: string;
  body: string;
  warnings: string[];
}

export interface AitoearnPreviewRow {
  account_id: string;
  platform: string;
  nickname: string;
  status: 'ready' | 'repairable' | 'blocked';
  ok: boolean;
  reject_reason?: string | null;
  warnings: string[];
  issues: Array<{
    code: string;
    severity: 'repairable' | 'fatal';
    message: string;
    media_index?: number | null;
    action?: string | null;
  }>;
  images: Array<{
    index: number;
    url: string;
    filename: string;
    format?: string | null;
    bytes: number;
    width?: number | null;
    height?: number | null;
    aspect_ratio?: number | null;
    sha256?: string | null;
    planned_actions: string[];
  }>;
  title: string;
  body: string;
  body_len: number;
  max_body?: number | null;
  image_count: number;
  need_cover?: boolean;
}

export const contentApi = {
  async listDocs(
    topicId: number,
    opts: {
      status?: DocStatus | 'to_review' | 'all';
      source?: DocSource | 'all';
      query_hit?: 'hit' | 'miss';   // ROI filter on the results report page
    },
    token: string,
  ): Promise<ContentDoc[]> {
    const qs = new URLSearchParams();
    if (opts.status && opts.status !== 'all') qs.set('status', opts.status);
    if (opts.source && opts.source !== 'all') qs.set('source', opts.source);
    if (opts.query_hit) qs.set('query_hit', opts.query_hit);
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    return request('GET', `/topics/${topicId}/docs${suffix}`, token);
  },
  async stats(topicId: number, token: string): Promise<ContentStats> {
    return request('GET', `/topics/${topicId}/stats`, token);
  },
  async getDoc(docId: number, token: string): Promise<ContentDoc> {
    return request('GET', `/docs/${docId}`, token);
  },
  /** Withdraw a placement task that hasn't gone out yet. Paid tasks can only be withdrawn while planned (after ordering, a refund is required). */
  async withdrawPublishTask(taskId: number, reason: string, token: string): Promise<PublishTask> {
    return request('POST', `/publish-tasks/${taskId}/withdraw`, token, { reason });
  },
  async submitMyDoc(
    topicId: number, payload: UserDocSubmitPayload, token: string,
  ): Promise<ContentDoc> {
    return request('POST', `/topics/${topicId}/docs`, token, payload);
  },
  async updateMyDoc(
    docId: number, payload: UserDocUpdatePayload, token: string,
  ): Promise<ContentDoc> {
    return request('PATCH', `/docs/${docId}`, token, payload);
  },
  async submitForReview(docId: number, token: string): Promise<ContentDoc> {
    return request('POST', `/docs/${docId}/submit-review`, token);
  },
  async approveMyDoc(docId: number, token: string): Promise<ContentDoc> {
    return request('POST', `/docs/${docId}/approve`, token);
  },
  async rejectMyDoc(docId: number, reason: string, token: string): Promise<ContentDoc> {
    return request('POST', `/docs/${docId}/reject`, token, { reason });
  },
  async configureAutoGenerate(
    topicId: number, payload: AutoGenerateConfig, token: string,
  ): Promise<AutoGenerateState> {
    return request('PATCH', `/topics/${topicId}/auto-generate`, token, payload);
  },
  async runNow(topicId: number, token: string): Promise<{ queued: number; topic_id: number }> {
    return request('POST', `/topics/${topicId}/run-now`, token);
  },
  // "Choose publishing": create publish tasks for an approved draft (one per outlet, awaiting URL backfill)
  async createPublishTasks(
    docId: number, targets: PublishTargetIn[], token: string,
  ): Promise<PublishTask[]> {
    return request('POST', `/docs/${docId}/publish-tasks`, token, { targets });
  },
  async listPublishTasks(
    topicId: number, opts: { status?: PublishTaskStatus; doc_id?: number }, token: string,
  ): Promise<PublishTask[]> {
    const qs = new URLSearchParams();
    if (opts.status) qs.set('status', opts.status);
    if (opts.doc_id) qs.set('doc_id', String(opts.doc_id));
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    return request('GET', `/topics/${topicId}/publish-tasks${suffix}`, token);
  },
  // Backfill the real article URL -> register a retest, enter the reward loop
  async submitPublishUrl(
    taskId: number, payload: SubmitUrlPayload, token: string,
  ): Promise<PublishTask> {
    return request('POST', `/publish-tasks/${taskId}/submit-url`, token, payload);
  },
  async confirmPublishCandidateUrl(taskId: number, token: string): Promise<PublishTask> {
    return request('POST', `/publish-tasks/${taskId}/confirm-candidate-url`, token);
  },
  async reportPublishVerification(
    taskId: number, payload: PublishVerificationReport, token: string,
  ): Promise<PublishTask> {
    return request('POST', `/publish-tasks/${taskId}/report-verification`, token, payload);
  },
  async refreshPublishVerification(taskId: number, token: string): Promise<PublishTask> {
    return request('POST', `/publish-tasks/${taskId}/refresh-verification`, token);
  },

  // ── Paid media channels (Ruanwenjiang / Meijieku, the same set of endpoints routed by provider) ────────
  // ── The following four hit the supplier sites' live APIs; the backend restricts them to admin/operator.
  //    User-side outlet selection goes through services/mediaApi.ts (mirror table, merged across suppliers, vendors not exposed).
  // Site list (for ops to check channel health)
  async rwjProviders(token: string): Promise<RwjProvidersResult> {
    return requestAt('/rwj', 'GET', '/providers', token);
  },
  async rwjStatus(token: string, provider?: string): Promise<RwjStatus> {
    return requestAt('/rwj', 'GET', `/status${provider ? `?provider=${provider}` : ''}`, token);
  },
  async searchRwjMedia(
    opts: {
      provider?: string;                  // rwj = Ruanwenjiang / mjk = Meijieku, default Ruanwenjiang
      type?: RwjResourceType;
      title?: string; price_min?: string; price_max?: string; page?: number;
      sort?: RwjSort;
      filters?: Record<string, number>;   // {field_1: 1001, ...} dimension filters
    },
    token: string,
  ): Promise<RwjMediaSearchResult> {
    return requestAt('/rwj', 'POST', '/media/search', token, { type: 'Website', ...opts });
  },
  // Media filter dimensions (consistent with the filter rows of that site's member center)
  async rwjMediaFilters(
    token: string, type: RwjResourceType = 'Website', provider?: string,
  ): Promise<RwjMediaFilters> {
    const q = `?type=${type}${provider ? `&provider=${provider}` : ''}`;
    return requestAt('/rwj', 'GET', `/media/filters${q}`, token);
  },
  // Push the draft to the platform's draft box (no charge) and create a "draft synced · pending publish" task -- multiple outlets at once,
  // merged into one draft on the platform, with one task per outlet (each with its own resource and quote)
  async pushRwjDrafts(
    docId: number, resources: { resource_id: number; resource_title: string }[],
    token: string, type: RwjResourceType = 'Website', provider?: string,
  ): Promise<RwjPushDraftResult> {
    return requestAt('/rwj', 'POST', `/docs/${docId}/push-draft`, token, {
      type, provider, resources,
    });
  },
  // Price check before publishing, getting a confirmation token bound to the price
  async rwjPublishPreview(taskId: number, token: string): Promise<RwjPublishPreview> {
    return requestAt('/rwj', 'POST', `/publish-tasks/${taskId}/publish-preview`, token);
  },
  // Real order (charges). No second confirmation; sends if the balance suffices.
  // expected_price_cents = the external quote the user saw on the page; the server checks the price live before ordering,
  // returns 409 + the new price if the increase exceeds the tolerance, orders directly within tolerance, and always allows price drops.
  async rwjPublish(
    taskId: number, expectedPriceCents: number | null, token: string,
  ): Promise<PublishTask> {
    return requestAt('/rwj', 'POST', `/publish-tasks/${taskId}/publish`, token,
                     { expected_price_cents: expectedPriceCents });
  },
  // Batch price check: get live external prices per outlet + total in one go
  async rwjBatchPublishPreview(taskIds: number[], token: string): Promise<RwjBatchPreview> {
    return requestAt('/rwj', 'POST', '/publish-tasks/batch-publish-preview', token,
                     { task_ids: taskIds });
  },
  // Order outlet by outlet serially (charges); an account-level failure aborts the remaining outlets
  async rwjBatchPublish(
    items: { task_id: number; expected_price_cents: number | null }[], token: string,
  ): Promise<RwjBatchPublishResult> {
    return requestAt('/rwj', 'POST', '/publish-tasks/batch-publish', token, { items });
  },
  /** Cross-topic "My placements" overview. Multi-topic accounts can see the overall ledger without clicking through topic by topic. */
  async listMyPublishTasks(
    token: string, opts: { status?: string; limit?: number } = {},
  ): Promise<PublishTask[]> {
    const qs = new URLSearchParams();
    if (opts.status) qs.set('status', opts.status);
    qs.set('limit', String(opts.limit ?? 50));
    return request<PublishTask[]>('GET', `/my/publish-tasks?${qs}`, token);
  },
  // Manually pull receipts for reconciliation
  async rwjReconcile(
    taskId: number, token: string,
  ): Promise<{ task: PublishTask; action: string; detail: string }> {
    return requestAt('/rwj', 'POST', `/publish-tasks/${taskId}/reconcile`, token);
  },
  // Manually trigger AI automatic outlet selection (backfill for old drafts; runs in the background, check results by polling the task list)
  async rwjAutoSelect(docId: number, token: string): Promise<{ scheduled: boolean; message: string }> {
    return requestAt('/rwj', 'POST', `/docs/${docId}/auto-select`, token);
  },

  // ── aitoearn multi-platform distribution ──────────────────────────────────────
  async aitoearnStatus(token: string): Promise<AitoearnStatus> {
    return requestAt('/aitoearn', 'GET', '/status', token);
  },

  async aitoearnAccounts(token: string, refresh = false): Promise<{ accounts: AitoearnAccount[] }> {
    return requestAt('/aitoearn', 'GET', `/accounts${refresh ? '?refresh=1' : ''}`, token);
  },

  async aitoearnPreview(
    docId: number, accountIds: string[], token: string, mediaIds: number[] = [],
  ): Promise<{
    doc_id: number;
    rows: AitoearnPreviewRow[];
    preflight_token: string;
    expires_at: string;
  }> {
    return requestAt('/aitoearn', 'POST', `/docs/${docId}/preview`, token, {
      account_ids: accountIds, media_ids: mediaIds,
    });
  },

  async aitoearnBindings(token: string): Promise<{ accounts: AitoearnAccount[] }> {
    return requestAt('/aitoearn', 'GET', '/bindings', token);
  },

  async aitoearnPublish(
    docId: number, accountIds: string[], token: string, mediaIds: number[] = [],
    preflightToken?: string,
  ): Promise<{ doc_id: number; results: Array<{ task: PublishTask; action: string; detail: string }> }> {
    return requestAt('/aitoearn', 'POST', `/docs/${docId}/publish`, token, {
      account_ids: accountIds, media_ids: mediaIds, preflight_token: preflightToken,
    });
  },

  async aitoearnStartAuth(platform: string, token: string): Promise<AitoearnAuthStart> {
    return requestAt('/aitoearn', 'POST', `/accounts/auth/${encodeURIComponent(platform)}`, token);
  },

  async aitoearnAuthStatus(
    attemptId: string, token: string,
  ): Promise<{
    status: 'pending' | 'completed' | 'failed'; error?: string; accounts?: AitoearnAccount[];
    requires_selection?: boolean;
    selectable_accounts?: Array<{ platformUid: string; account?: string; displayName?: string }>;
  }> {
    return requestAt('/aitoearn', 'GET', `/accounts/auth-attempts/${encodeURIComponent(attemptId)}`, token);
  },

  async aitoearnSubmitAuthSelections(
    attemptId: string, accounts: Array<{ platformUid: string; account?: string }>, token: string,
  ): Promise<{ submitted: boolean }> {
    return requestAt(
      '/aitoearn', 'POST', `/accounts/auth-attempts/${encodeURIComponent(attemptId)}/selections`,
      token, { accounts },
    );
  },

  async aitoearnUnbind(bindingId: number, token: string): Promise<{ deleted: boolean }> {
    return requestAt('/aitoearn', 'DELETE', `/accounts/${bindingId}`, token);
  },

  async aitoearnPluginPublishStart(
    docId: number, accountId: string, token: string, mediaIds: number[] = [],
  ): Promise<AitoearnPluginStartResult> {
    return requestAt('/aitoearn', 'POST', `/docs/${docId}/plugin-publish/start`, token, {
      account_id: accountId, media_ids: mediaIds,
    });
  },

  async aitoearnPluginPublishComplete(
    taskId: number,
    result: { success: boolean; share_link?: string; work_id?: string; error?: string },
    token: string,
  ): Promise<{ task: PublishTask; action: 'url_submitted' | 'pending_url' | 'failed' }> {
    return requestAt('/aitoearn', 'POST', `/plugin-publish-tasks/${taskId}/complete`, token, result);
  },

  async aitoearnReconcile(
    taskId: number, token: string,
  ): Promise<{ task: PublishTask; action: string; detail: string }> {
    return requestAt('/aitoearn', 'POST', `/publish-tasks/${taskId}/reconcile`, token);
  },
};
