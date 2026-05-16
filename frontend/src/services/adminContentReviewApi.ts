// Admin 内容审核 API — Phase D.
// 端点都挂在 /api/admin/content-review,后端 require_admin 守门.

import { localizedHeaders, readApiError } from './apiError';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

export type DocStatus = 'draft' | 'pending_review' | 'approved' | 'rejected' | 'published';

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
  marked_at?: string;
  marked_by?: number;
}

export interface GeneratedDoc {
  id: number;
  topic_id: number;
  execution_plan_id?: number | null;
  created_at: string;
  source_query_text: string;
  title: string;
  body_markdown: string;
  summary: string;
  llm_model: string;
  generation_error?: string | null;
  status: DocStatus;
  selected_for_review: boolean;
  review_decision_at?: string | null;
  reviewer_id?: number | null;
  reject_reason?: string | null;
  publish_targets: PublishTarget[];
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

export const adminContentReviewApi = {
  async listTopics(token: string): Promise<TopicWithDocs[]> {
    return request('GET', '/topics', token);
  },
  async listDocs(topicId: number, status: string | undefined, token: string): Promise<GeneratedDoc[]> {
    const qs = status ? `?status=${status}` : '';
    return request('GET', `/topics/${topicId}/docs${qs}`, token);
  },
  async getDoc(docId: number, token: string): Promise<GeneratedDoc> {
    return request('GET', `/docs/${docId}`, token);
  },
  async selectForReview(docIds: number[], token: string): Promise<void> {
    return request('POST', '/docs/select', token, { doc_ids: docIds });
  },
  async approveDoc(docId: number, token: string): Promise<GeneratedDoc> {
    return request('POST', `/docs/${docId}/approve`, token);
  },
  async rejectDoc(docId: number, reason: string, token: string): Promise<GeneratedDoc> {
    return request('POST', `/docs/${docId}/reject`, token, { reason });
  },
  async publishDoc(docId: number, targets: { platform: string; media: string }[], token: string): Promise<GeneratedDoc> {
    return request('POST', `/docs/${docId}/publish`, token, { publish_targets: targets });
  },
};
