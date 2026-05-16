// Phase D — 用户侧 topic 画像 / 监测问题勾选 / 提交审核 API client.
//
// 服务端关联路由:backend/geo/api/ai_telemetry.py
//  PUT  /api/ai-telemetry/topics/{id}/profile
//  POST /api/ai-telemetry/topics/{id}/expand-queries
//  POST /api/ai-telemetry/topics/{id}/selected-queries
//  POST /api/ai-telemetry/topics/{id}/submit-for-review

import { localizedHeaders, readApiError } from './apiError';
import type { BrandProfile, Topic } from './aiTelemetryApi';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

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

export interface ExpandQueriesResp {
  seed: string;
  queries: string[];
  model?: string;
}

export interface SelectedItem {
  text: string;
  selected: boolean;
}

export interface ExtractProfileResp {
  profile: BrandProfile;
  used_model: string;
}

export const topicProfileApi = {
  async updateProfile(topicId: number, profile: BrandProfile, token: string): Promise<Topic> {
    return request<Topic>('PUT', `/topics/${topicId}/profile`, token, profile);
  },
  async expandQueries(topicId: number, seed: string, count: number, token: string): Promise<ExpandQueriesResp> {
    return request<ExpandQueriesResp>(
      'POST', `/topics/${topicId}/expand-queries`, token, { seed, count },
    );
  },
  async updateSelectedQueries(topicId: number, items: SelectedItem[], token: string): Promise<Topic> {
    return request<Topic>(
      'POST', `/topics/${topicId}/selected-queries`, token, { items },
    );
  },
  async submitForReview(topicId: number, token: string): Promise<Topic> {
    return request<Topic>('POST', `/topics/${topicId}/submit-for-review`, token);
  },
  async extractProfile(text: string, token: string): Promise<ExtractProfileResp> {
    return request<ExtractProfileResp>('POST', '/profile/extract', token, { text });
  },
};
