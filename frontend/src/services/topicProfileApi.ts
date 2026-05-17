// Phase D — 用户侧 topic 资料 / 监测问题勾选 / 提交审核 API client.
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
  // PDF / Word / 纯文本文件 — 后端用 pypdf / python-docx 解析后走同一条
  // LLM pipeline,所以前端 PDF.js / mammoth 都不用上,bundle 不胖。
  async extractProfileFile(file: File, token: string): Promise<ExtractProfileResp> {
    const fd = new FormData();
    fd.append('file', file, file.name);
    const resp = await fetch(`${API_BASE}/ai-telemetry/profile/extract-file`, {
      method: 'POST',
      headers: localizedHeaders({ Authorization: `Bearer ${token}` }),
      body: fd,
    });
    if (!resp.ok) {
      const msg = await readApiError(resp, 'AI 解析失败');
      throw new Error(msg);
    }
    return resp.json();
  },
};
