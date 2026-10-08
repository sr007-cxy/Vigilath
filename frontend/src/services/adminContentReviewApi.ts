// Admin content review API — Phase D.
// All endpoints live under /api/admin/content-review, guarded by backend require_admin.

import { localizedHeaders, readApiError } from './apiError';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

export type DocStatus = 'generating' | 'draft' | 'pending_review' | 'approved' | 'rejected' | 'published';
export type DocSource = 'ai' | 'user';

export interface TopicWithDocs {
  topic_id: number;
  topic_name: string;
  user_id: number;
  user_email: string;
  doc_count: number;
  draft_count: number;
  pending_review_count: number;
  approved_count: number;
  published_count: number;
  rejected_count: number;
}

export interface PublishTarget {
  platform: string;
  media: string;
  url?: string;
  marked_at?: string;
  marked_by?: number;
}

// Review details from the multi-agent refinement pipeline (backend services/content_quality.py).
// Five dimensions: question coverage / citability / facts and information density / brand consistency / layout and length.
export interface DocQuality {
  pipeline?: string;
  score?: number | null;
  dims?: Partial<Record<'coverage' | 'quotability' | 'evidence' | 'brand' | 'format', number>>;
  uncovered?: string[];      // Prompts not explicitly answered
  blocking?: string[];       // Must fix, otherwise can't publish
  fixes?: string[];          // Revision list issued by the review
  rounds?: number;           // Actual number of revision rounds
  notes?: string[];          // Degradation notes (some agent failed)
  outline?: { angle?: string; direct_answers?: number; sections?: number };
}

export interface GeneratedDoc {
  id: number;
  topic_id: number;
  execution_plan_id?: number | null;
  created_at: string;
  // Last write time. Regeneration overwrites in place (created_at unchanged), so only this shows the body was rewritten
  updated_at?: string | null;
  source_query_text: string;
  title: string;
  body_markdown: string;
  summary: string;
  llm_model: string;
  generation_error?: string | null;
  // 2026-08-04 — re-queue state for failed generation: number of automatic retries so far + next retry time (null = not in the queue)
  gen_attempts?: number;
  gen_next_retry_at?: string | null;
  // 2026-08-04 — review result of multi-agent refinement (null = review not run / pipeline off)
  quality_score?: number | null;
  quality?: DocQuality | null;
  status: DocStatus;
  selected_for_review: boolean;
  review_decision_at?: string | null;
  reviewer_id?: number | null;
  reject_reason?: string | null;
  publish_targets: PublishTarget[];
  source: DocSource;
  // 2026-05-28 — multi-variant generation combo identifiers (multiple drafts for the same query are distinguished by these two)
  creation_direction?: string | null;
  copywriting_type?: string | null;
  // Mediumsly push status: success -> post_id + url + pushed_at; failure -> last_error
  mediumsly_post_id?: string | null;
  mediumsly_url?: string | null;
  mediumsly_pushed_at?: string | null;
  mediumsly_last_error?: string | null;
  // 2026-06-12 — backfilled platform review result (passed/rejected; rejected reasons are material for rule learning)
  platform_review_status?: 'passed' | 'rejected' | null;
  platform_reject_reason?: string | null;
  // 2026-07-31 — back-link to the publish plan row: which plan row produced this draft (empty for user-supplied drafts)
  plan_item_id?: string | null;
  plan_seq?: number | null;              // #N in the plan table (from 1)
  plan_publish_date?: string | null;     // Planned publish date (YYYY-MM-DD or with THH:MM)
  // 2026-07-28 — template used for generation (content_templates); empty for user-supplied drafts / legacy without a template
  template_id?: number | null;
  template_name?: string | null;
  template_kind?: string | null;
  // 2026-08-05 — writing links: which seed prompt it was written from, which prompts it should cover, which platform it targets
  // Content ID (implicit label under the "Measures for Labeling AI-Generated Synthetic Content"). The detail page's explicit label bar shows it and links to
  // the provenance page; empty for existing drafts from before the feature launched. IP isn't sent to the review page, only to the provenance page.
  gen_trace_no?: string | null;
  plan_seed?: string | null;
  plan_queries?: string[];
  plan_platform?: string | null;
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
  const resp = await fetch(`${API_BASE}/admin/content-review${path}`, init);
  if (!resp.ok) {
    const msg = await readApiError(resp, `Request ${method} ${path} failed`);
    throw new Error(msg);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json() as Promise<T>;
}

// Flywheel P1-C — targeted post-placement retest probe (before vs. after publication metric comparison)
export interface RetestMetrics {
  responses?: number; hit_pct?: number; top3_pct?: number; cited_own_pct?: number;
}
export interface RetestProbe {
  id: number;
  doc_id: number;
  status: 'scheduled' | 'enqueued' | 'done' | 'cancelled';
  queries: string[];
  engines: string[];
  baseline: RetestMetrics;
  result: { before?: RetestMetrics; after?: RetestMetrics; delta?: Record<string, number> };
  scheduled_for?: string | null;
  enqueued_at?: string | null;
  completed_at?: string | null;
  error?: string | null;
}

/** "Why this article was written this way" -- what was injected at generation, recomputed with the current config (not an archive from back then) */
export interface DocRationale {
  doc_id: number;
  topic_id: number;
  generated_at?: string | null;
  recomputed_at?: string | null;
  llm_model?: string;
  route?: 'template' | 'profile_combo';
  template?: { id: number; name: string; kind: string;
               length_min?: number; length_max?: number; english?: boolean } | null;
  combo?: { creation_direction?: string | null; copywriting_type?: string | null;
            length_hint?: string | null; source?: string };
  platform?: string;
  plan_row?: {
    item_id?: string; seq?: number; publish_date?: string; seed?: string | null;
    queries?: string[]; platform?: string; channel?: string; media?: string;
    note?: string | null; origin?: Record<string, unknown> | null;
    why?: Record<string, unknown> | null;
  } | null;
  blocks?: {
    key: string; title: string;
    text?: string; items?: unknown[]; count?: number;
    primary?: string; secondary?: string[];
    cited_count?: number;
    /** true = body shown only to ops (raw platform red lines, itemized citation requirements) */
    staff_only_text?: boolean;
    source?: 'settled' | 'recomputed' | 'live_config';
    updated_at?: string | null; learned_at?: string | null;
  }[];
  quality?: { score?: number | null; detail?: Record<string, unknown> | null };
  caveat?: string;
  error?: string;
}

export const adminContentReviewApi = {
  async getRetest(docId: number, token: string): Promise<RetestProbe[]> {
    return request('GET', `/docs/${docId}/retest`, token);
  },
  // Fetched per article on demand, deliberately not in the list endpoint (each item would need platform rules + competitor aggregation, noticeably slowing the list)
  async getRationale(docId: number, token: string): Promise<DocRationale> {
    return request('GET', `/docs/${docId}/rationale`, token);
  },

  async listTopics(token: string): Promise<TopicWithDocs[]> {
    return request('GET', '/topics', token);
  },
  async listDocs(
    topicId: number,
    status: string | undefined,
    token: string,
    source?: DocSource,
  ): Promise<GeneratedDoc[]> {
    const qs = new URLSearchParams();
    if (status) qs.set('status', status);
    if (source) qs.set('source', source);
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    return request('GET', `/topics/${topicId}/docs${suffix}`, token);
  },
  async getDoc(docId: number, token: string): Promise<GeneratedDoc> {
    return request('GET', `/docs/${docId}`, token);
  },
  // Compliance rescan -- unlocks after a rejected draft is edited / the lexicon is adjusted: pass -> automatically back to draft, fail -> refresh the rejection reason
  async rescanDoc(docId: number, token: string): Promise<GeneratedDoc> {
    return request('POST', `/docs/${docId}/rescan`, token);
  },
  // Admin edits the body of AI / user drafts (any source / status); editing a rejected draft triggers an automatic rescan
  async updateDoc(
    docId: number,
    payload: { title?: string; body_markdown?: string; summary?: string },
    token: string,
  ): Promise<GeneratedDoc> {
    return request('PATCH', `/docs/${docId}`, token, payload);
  },
  async selectForReview(docIds: number[], token: string): Promise<void> {
    return request('POST', '/docs/select', token, { doc_ids: docIds });
  },
  async approveDoc(docId: number, token: string): Promise<GeneratedDoc> {
    return request('POST', `/docs/${docId}/approve`, token);
  },
  // Regenerate a single draft — runs the LLM synchronously and returns the overwritten doc (may take 30-60s)
  async regenerateDoc(docId: number, token: string): Promise<GeneratedDoc> {
    return request('POST', `/docs/${docId}/regenerate`, token);
  },

  // Give up automatic retries: dequeue but keep the error cause; manual regeneration is still possible
  async cancelDocRetry(docId: number, token: string): Promise<GeneratedDoc> {
    return request('POST', `/docs/${docId}/cancel-retry`, token);
  },
  async rejectDoc(docId: number, reason: string, token: string): Promise<GeneratedDoc> {
    return request('POST', `/docs/${docId}/reject`, token, { reason });
  },
  async publishDoc(
    docId: number,
    targets: { platform: string; media: string; url?: string }[],
    token: string,
    pushToMediumsly = false,
  ): Promise<GeneratedDoc> {
    return request('POST', `/docs/${docId}/publish`, token, {
      publish_targets: targets,
      push_to_mediumsly: pushToMediumsly,
    });
  },
  // Backfill the platform's review result (published drafts only); rejected must include a reason, for platform rule learning
  async setPlatformReview(
    docId: number,
    status: 'passed' | 'rejected',
    token: string,
    reason?: string,
  ): Promise<GeneratedDoc> {
    return request('POST', `/docs/${docId}/platform-review`, token, { status, reason });
  },
};
