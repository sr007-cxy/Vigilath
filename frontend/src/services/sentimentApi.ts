// Sentiment monitoring backend API client.
// All methods go through /api/sentiment/* and need a Bearer token (injected by AuthContext).
// With VITE_USE_MOCK_SENTIMENT=1 there's no network; frontend components read mocks/sentiment.ts directly.

import { localizedHeaders, readApiError } from './apiError';
import type {
  AccountCreatePayload, AccountUpdatePayload,
  Brief, BriefsResponse, DraftGenerateResult, DraftsResponse,
  KnowledgeDoc, NewsnowHotResponse, NewsnowSourcesResponse,
  PostsResponse, PostsQuery, RunStatusPayload,
  SentimentAccount, SentimentPlatform, SentimentWebSearchLatestResponse, TodayResponse,
} from '../types/sentiment';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

/** Whether to use mock data (set VITE_USE_MOCK_SENTIMENT=1 in .env / at startup).
 *  Sentiment.tsx / each tab decide between mock and the real API via isMockMode(). */
export function isMockMode(): boolean {
  return String(import.meta.env.VITE_USE_MOCK_SENTIMENT || '').toLowerCase() === '1';
}

function authHeaders(token: string, extra: Record<string, string> = {}): Record<string, string> {
  return localizedHeaders({
    Authorization: `Bearer ${token}`,
    ...extra,
  });
}

/** Timeout for write operations: these backend calls are pure DB writes (batch runs are a BackgroundTask, not holding the response),
 *  normally returning within 1s. 30s is set so that the state "clicked save and it spins forever, neither succeeding nor erroring"
 *  **can't** exist -- either it succeeds, or it gives a message that can be followed up on.
 *  Fetch endpoints have no timeout (briefs/articles really are slow when large); only writes are capped. */
const WRITE_TIMEOUT_MS = 30_000;

async function request<T>(
  method: string, path: string, token: string,
  body?: unknown,
  timeoutMs?: number,
): Promise<T> {
  const init: RequestInit = {
    method,
    headers: authHeaders(token, body !== undefined ? { 'Content-Type': 'application/json' } : {}),
  };
  if (body !== undefined) init.body = JSON.stringify(body);

  const ctrl = timeoutMs ? new AbortController() : null;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs) : null;
  if (ctrl) init.signal = ctrl.signal;

  let resp: Response;
  try {
    resp = await fetch(`${API_BASE}/sentiment${path}`, init);
  } catch (e) {
    if (ctrl?.signal.aborted) {
      throw new Error(`请求超时(${Math.round((timeoutMs ?? 0) / 1000)}s 无响应):${method} ${path}。`
        + '服务端没有返回,请把这条信息发给运维查后端日志。');
    }
    throw e;
  } finally {
    if (timer) clearTimeout(timer);
  }

  if (!resp.ok) {
    const msg = await readApiError(resp, `Request ${method} ${path} failed`);
    throw new Error(msg);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json() as Promise<T>;
}

// ─────────────────────────── Account CRUD ────────────────────────

/** State machine of a one-off quick check. Only ready has a result. */
export interface SentimentOnceState {
  brand: string;
  state: 'ready' | 'running' | 'started' | 'failed' | 'stale' | 'none' | 'timeout' | 'degraded';
  result?: Record<string, unknown>;
  elapsed_s?: number;
  error?: string;
}

export const sentimentApi = {
  /** Start a one-off quick check. No account created, not in batch runs; results go into a 24h cache. */
  startOnce(brand: string, keywords: string[], token: string): Promise<SentimentOnceState> {
    return request<SentimentOnceState>('POST', '/once', token, { brand, keywords });
  },

  /** Poll this check. A round takes up to 360s, so it must be polled, not awaited inline. */
  getOnce(brand: string, keywords: string[], token: string): Promise<SentimentOnceState> {
    const qs = new URLSearchParams({ brand });
    if (keywords.length) qs.set('keywords', keywords.join(','));
    return request<SentimentOnceState>('GET', `/once?${qs}`, token);
  },

  listPlatforms(token: string): Promise<SentimentPlatform[]> {
    return request<SentimentPlatform[]>('GET', '/platforms', token);
  },

  listAccounts(token: string): Promise<SentimentAccount[]> {
    return request<SentimentAccount[]>('GET', '/accounts', token);
  },

  createAccount(payload: AccountCreatePayload, token: string): Promise<SentimentAccount> {
    return request<SentimentAccount>('POST', '/accounts', token, payload, WRITE_TIMEOUT_MS);
  },

  getAccount(accountId: number, token: string): Promise<SentimentAccount> {
    return request<SentimentAccount>('GET', `/accounts/${accountId}`, token);
  },

  updateAccount(
    accountId: number, payload: AccountUpdatePayload, token: string,
  ): Promise<SentimentAccount> {
    return request<SentimentAccount>('PUT', `/accounts/${accountId}`, token, payload, WRITE_TIMEOUT_MS);
  },

  deleteAccount(accountId: number, token: string): Promise<void> {
    return request<void>('DELETE', `/accounts/${accountId}`, token, undefined, WRITE_TIMEOUT_MS);
  },

  // ── State machine / trigger ──
  getRunStatus(accountId: number, token: string): Promise<RunStatusPayload> {
    return request<RunStatusPayload>('GET', `/accounts/${accountId}/run-status`, token);
  },

  runNow(accountId: number, token: string): Promise<{ queued: boolean }> {
    return request<{ queued: boolean }>(
      'POST', `/accounts/${accountId}/run-now`, token, undefined, WRITE_TIMEOUT_MS,
    );
  },

  listRunLogs(accountId: number, token: string, limit = 20) {
    return request<unknown[]>(
      'GET', `/accounts/${accountId}/run-logs?limit=${limit}`, token,
    );
  },

  getWebsearchLatest(accountId: number, token: string): Promise<SentimentWebSearchLatestResponse> {
    return request<SentimentWebSearchLatestResponse>(
      'GET', `/accounts/${accountId}/websearch-latest`, token,
    );
  },

  // ── Data queries ──
  getToday(accountId: number, ticker: string, token: string, days = 7): Promise<TodayResponse> {
    return request<TodayResponse>(
      'GET',
      `/accounts/${accountId}/today?ticker=${encodeURIComponent(ticker)}&days=${days}`,
      token,
    );
  },

  listPosts(
    accountId: number, ticker: string, token: string,
    query: PostsQuery = {},
  ): Promise<PostsResponse> {
    const limit = query.limit ?? 50;
    const offset = query.offset ?? 0;
    const usp = new URLSearchParams();
    usp.set('ticker', ticker);
    usp.set('limit', String(limit));
    usp.set('offset', String(offset));
    // start/end take precedence; if start/end are given the backend ignores days, but days is still passed through for compatibility
    if (query.days !== undefined) usp.set('days', String(query.days));
    if (query.start) usp.set('start', query.start);
    if (query.end) usp.set('end', query.end);
    if (query.sort_by) usp.set('sort_by', query.sort_by);
    return request<PostsResponse>(
      'GET',
      `/accounts/${accountId}/posts?${usp.toString()}`,
      token,
    );
  },

  listBriefs(accountId: number, ticker: string, token: string): Promise<BriefsResponse> {
    return request<BriefsResponse>(
      'GET',
      `/accounts/${accountId}/briefs?ticker=${encodeURIComponent(ticker)}`,
      token,
    );
  },

  getBrief(accountId: number, briefId: number, token: string): Promise<Brief> {
    return request<Brief>('GET', `/accounts/${accountId}/briefs/${briefId}`, token);
  },

  listDrafts(accountId: number, ticker: string, token: string): Promise<DraftsResponse> {
    return request<DraftsResponse>(
      'GET',
      `/accounts/${accountId}/drafts?ticker=${encodeURIComponent(ticker)}`,
      token,
    );
  },

  generateDraft(
    payload: { account_id: number; source?: string; post_id?: string; topic?: string; situation?: string },
    token: string,
  ): Promise<DraftGenerateResult> {
    return request<DraftGenerateResult>('POST', '/drafts/generate', token, payload);
  },

  // ── newsnow real-time trending (not bound to an account) ──
  listNewsnowSources(token: string): Promise<NewsnowSourcesResponse> {
    return request<NewsnowSourcesResponse>('GET', '/newsnow/sources', token);
  },

  getNewsnowHot(source: string, token: string, limit = 30): Promise<NewsnowHotResponse> {
    const usp = new URLSearchParams({ source, limit: String(limit) });
    return request<NewsnowHotResponse>('GET', `/newsnow/hot?${usp.toString()}`, token);
  },

  // ── Knowledge base ──
  listKnowledge(accountId: number, token: string): Promise<KnowledgeDoc[]> {
    return request<KnowledgeDoc[]>('GET', `/accounts/${accountId}/knowledge`, token);
  },

  upsertKnowledge(
    accountId: number, key: string, body: string, token: string,
  ): Promise<KnowledgeDoc> {
    return request<KnowledgeDoc>(
      'PUT', `/accounts/${accountId}/knowledge/${key}`, token, { body },
    );
  },
};

export default sentimentApi;
