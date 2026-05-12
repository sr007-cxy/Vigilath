// AI 遥测 API 客户端 — 话题 CRUD + 立即试跑.
// 后端就绪前用 mock 数据;VITE_USE_MOCK_AI_TELEMETRY=1 强制 mock.

import { localizedHeaders, readApiError } from './apiError';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

export type EngineId =
  | 'deepseek' | 'doubao' | 'qwen' | 'wenxin' | 'yuanbao'
  | 'chatgpt' | 'claude' | 'gemini' | 'grok' | 'copilot';

export const CN_ENGINES: EngineId[] = ['deepseek', 'doubao', 'qwen', 'wenxin', 'yuanbao'];
export const GLOBAL_ENGINES: EngineId[] = ['chatgpt', 'claude', 'gemini', 'grok', 'copilot'];

export interface Topic {
  id: number;
  name: string;
  queries: string[];
  engines: EngineId[];
  enabled: boolean;
  last_run_at?: string | null;
  last_run_status?: 'success' | 'failed' | 'running' | null;
  created_at?: string;
  updated_at?: string;
}

export interface TopicPayload {
  name: string;
  queries: string[];
  engines: EngineId[];
  enabled: boolean;
}

export interface RunNowResult {
  engine: EngineId;
  query: string;
  answer: string;
  citations: { url: string; domain: string; title: string }[];
  error?: string | null;
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
  error: string | null;
  created_at: string;
}

export function isMockMode(): boolean {
  return String(import.meta.env.VITE_USE_MOCK_AI_TELEMETRY || '').toLowerCase() === '1';
}

// ── mock state ──────────────────────────────────────────────
let _mockSeq = 100;
const _mockTopics: Topic[] = [
  {
    id: 1,
    name: 'VNET 品牌问询',
    queries: ['世纪互联怎么样', '国内 IDC 服务商推荐', 'VNET vs 万国数据'],
    engines: ['deepseek', 'doubao', 'qwen', 'wenxin', 'yuanbao', 'chatgpt'],
    enabled: true,
    last_run_at: new Date(Date.now() - 2 * 3600 * 1000).toISOString(),
    last_run_status: 'success',
  },
];

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

export const aiTelemetryApi = {
  async listTopics(token: string): Promise<Topic[]> {
    if (isMockMode()) return Promise.resolve([..._mockTopics]);
    return request<Topic[]>('GET', '/topics', token);
  },

  async createTopic(payload: TopicPayload, token: string): Promise<Topic> {
    if (isMockMode()) {
      const t: Topic = { id: _mockSeq++, ...payload, last_run_at: null, last_run_status: null };
      _mockTopics.push(t);
      return Promise.resolve(t);
    }
    return request<Topic>('POST', '/topics', token, payload);
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

  async triggerRun(topicId: number, token: string): Promise<{ status: string; topic_id: number }> {
    if (isMockMode()) return Promise.resolve({ status: 'started', topic_id: topicId });
    return request('POST', `/topics/${topicId}/run`, token);
  },

  async listRuns(topicId: number, token: string, limit = 20): Promise<RunSummary[]> {
    if (isMockMode()) return Promise.resolve([]);
    return request<RunSummary[]>('GET', `/topics/${topicId}/runs?limit=${limit}`, token);
  },

  async listResponses(runId: number, token: string): Promise<ResponseRow[]> {
    if (isMockMode()) return Promise.resolve([]);
    return request<ResponseRow[]>('GET', `/runs/${runId}/responses`, token);
  },
};
