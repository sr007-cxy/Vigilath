// Admin 审核 API — Phase C.
// 端点都挂在 /api/admin/review,后端用 require_admin 守门;调用方需带 admin token.

import { localizedHeaders, readApiError } from './apiError';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

export interface PendingSeedItem {
  topic_id: number;
  topic_name: string;
  target: string;
  user_id: number;
  user_email: string;
  idx: number;
  text: string;
  submitted_at?: string | null;
}

export interface PendingQueryItem {
  topic_id: number;
  topic_name: string;
  target: string;
  user_id: number;
  user_email: string;
  idx: number;
  text: string;
  cluster_id?: number | null;
  submitted_at?: string | null;
}

export interface PendingReview {
  seed_prompts: PendingSeedItem[];
  queries: PendingQueryItem[];
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
  const resp = await fetch(`${API_BASE}/admin/review${path}`, init);
  if (!resp.ok) {
    const msg = await readApiError(resp, `Request ${method} ${path} failed`);
    throw new Error(msg);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json() as Promise<T>;
}

export const adminReviewApi = {
  async listPending(token: string): Promise<PendingReview> {
    return request<PendingReview>('GET', '/pending', token);
  },
  async approveSeed(topicId: number, idx: number, token: string): Promise<void> {
    return request<void>('POST', `/seed/${topicId}/${idx}/approve`, token);
  },
  async rejectSeed(topicId: number, idx: number, token: string): Promise<void> {
    return request<void>('POST', `/seed/${topicId}/${idx}/reject`, token);
  },
  async approveQueries(topicId: number, indices: number[], token: string): Promise<void> {
    return request<void>('POST', `/queries/${topicId}/approve`, token, { indices });
  },
  async rejectQueries(topicId: number, indices: number[], token: string): Promise<void> {
    return request<void>('POST', `/queries/${topicId}/reject`, token, { indices });
  },
};
