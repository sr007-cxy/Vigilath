// Content template API — used by the execution plan editor.
// Endpoints are mounted at /api/admin/content-templates, guarded by backend require_admin.

import { localizedHeaders, readApiError } from './apiError';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

export type TemplateScope = 'system' | 'tenant' | 'topic';

export interface ContentTemplate {
  id: number;
  scope: TemplateScope;
  tenant_id?: number | null;
  topic_id?: number | null;
  name: string;
  kind: string;                          // long-form / short-form / FAQ / case study / review / comparison / tutorial / Q&A
  prompt_template: string;
  target_platforms: string[];
  length_min: number;
  length_max: number;
  locked: boolean;
  locked_at?: string | null;
  locked_by_id?: number | null;
  created_by_id?: number | null;
  created_at: string;
  updated_at: string;
}

export interface ContentTemplateCreatePayload {
  scope?: TemplateScope;                 // Default tenant
  topic_id?: number;
  name: string;
  kind?: string;
  prompt_template: string;
  target_platforms?: string[];
  length_min?: number;
  length_max?: number;
}

export interface ContentTemplateUpdatePayload {
  name?: string;
  kind?: string;
  prompt_template?: string;
  target_platforms?: string[];
  length_min?: number;
  length_max?: number;
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
  const resp = await fetch(`${API_BASE}/admin/content-templates${path}`, init);
  if (!resp.ok) {
    const msg = await readApiError(resp, `Request ${method} ${path} failed`);
    throw new Error(msg);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json() as Promise<T>;
}

// ── Template scorecard: how drafts from each template actually turned out (dominated by citation rate) ──────────
export interface TemplateScoreItem {
  template_id: number;
  name: string;
  kind: string;
  scope: string;
  missing: boolean;                      // Template deleted; stats kept for history
  score: number | null;                  // null = too few drafts, no score
  sample_enough: boolean;
  cited_measurable: boolean;             // false = missing placement links, so the score excludes the citation dimension
  rates: {                               // Percentages; null = no data for this dimension, excluded from scoring
    cited: number | null;
    compliance: number | null;
    review: number | null;
    platform: number | null;
  };
  docs: number;
  published: number;
  mature_published: number;              // Drafts published for at least mature_days days
  trackable_published: number;           // Of those, ones with real placement links whose citation can be judged (citation rate denominator)
  untracked_published: number;           // Missing links, citation can't be judged
  cited_docs: number;
  cited_engines: Record<string, number>;
  // 2026-08-04 — mean review score of multi-agent refinement (null = none of these drafts went through review).
  // A leading metric, not counted in score: citation rate needs 7 days to settle, while the refinement score is available on the drafting day.
  quality_avg: number | null;
  quality_docs: number;
  rejected_compliance: number;
  rejected_manual: number;
  approved: number;
  platform_passed: number;
  platform_rejected: number;
  first_used_at: string | null;
  last_used_at: string | null;
}

export interface TemplateScorecard {
  mature_days: number;
  min_sample: number;
  weights: Record<string, number>;
  items: TemplateScoreItem[];
}

export const contentTemplatesApi = {
  // Template scorecard (read-only aggregation) -- empty days = all
  async scorecard(
    token: string, opts: { topicId?: number; days?: number } = {},
  ): Promise<TemplateScorecard> {
    const qs = new URLSearchParams();
    if (opts.topicId !== undefined) qs.set('topic_id', String(opts.topicId));
    if (opts.days !== undefined) qs.set('days', String(opts.days));
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    return request<TemplateScorecard>('GET', `/scorecard${suffix}`, token);
  },
  async list(
    token: string, opts: { scope?: TemplateScope; topicId?: number } = {},
  ): Promise<ContentTemplate[]> {
    const qs = new URLSearchParams();
    if (opts.scope) qs.set('scope', opts.scope);
    if (opts.topicId !== undefined) qs.set('topic_id', String(opts.topicId));
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    return request<ContentTemplate[]>('GET', suffix, token);
  },
  async create(payload: ContentTemplateCreatePayload, token: string): Promise<ContentTemplate> {
    return request<ContentTemplate>('POST', '', token, payload);
  },
  async update(
    id: number, payload: ContentTemplateUpdatePayload, token: string,
  ): Promise<ContentTemplate> {
    return request<ContentTemplate>('PATCH', `/${id}`, token, payload);
  },
  async lock(id: number, token: string): Promise<ContentTemplate> {
    return request<ContentTemplate>('POST', `/${id}/lock`, token);
  },
  // Unlock — prompt / kind become editable again
  async unlock(id: number, token: string): Promise<ContentTemplate> {
    return request<ContentTemplate>('POST', `/${id}/unlock`, token);
  },
  // Delete template (locked ones must be unlocked first; drafts already using this template are only unbound, not deleted)
  async remove(id: number, token: string): Promise<void> {
    return request<void>('DELETE', `/${id}`, token);
  },
};
