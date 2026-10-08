// Model call usage (admin-only) — /api/admin/llm-usage
// Backend geo/api/admin_llm_usage.py. Metric definition: "call count" = SUM(requests_count), not row count;
// history comes from the daily summary table, today from live aggregation of details (live tail), stitched together in the backend.
import { readApiError, localizedHeaders } from './apiError';

export interface UsageKpi {
  calls: number;
  rows_n: number;
  ok_calls: number;            // Requests billed by the provider (ok / truncated / parse failure)
  error_calls: number;         // Wasted requests (HTTP error / network error)
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cached_tokens: number;
  reasoning_tokens: number;
  calls_with_tokens: number;
  calls_without_tokens: number;
  latency_ms_sum: number;
  users_n: number;
  error_rate_pct: number;
  avg_latency_ms: number;
  token_coverage_pct: number;  // Share of calls with tokens obtained; the page shows a badge when below 100%
}

export interface UsageOverview {
  range: { start: string; end: string; days: number; today: string };
  kpi: UsageKpi;
  delta: {
    calls_pct: number | null;        // null when the previous period was 0; the frontend shows "—"
    total_tokens_pct: number | null;
    error_rate_pp: number;
  };
}

export interface UsageTrendSeries {
  key: string;
  calls: number[];
  total_tokens: number[];
  prompt_tokens: number[];
  completion_tokens: number[];
  error_calls: number[];
}

export interface UsageTrend {
  days: string[];
  group_by: string;
  series: UsageTrendSeries[];
}

export interface UsageFeatureItem {
  feature: string;
  calls: number;
  rows_n: number;
  error_calls: number;
  error_rate_pct: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cached_tokens: number;
  avg_latency_ms: number;
  share_pct: number;
  calls_share_pct: number;
  top_model: string;
  sparkline: number[];
}

export interface UsageFeatureRank {
  range: { start: string; end: string };
  total_tokens_all: number;
  calls_all: number;
  items: UsageFeatureItem[];
}

export interface UsageLogItem {
  id: number;
  created_at: string | null;
  bj_date: string;
  service: string;
  host: string;
  feature: string;
  provider: string;
  model: string;
  endpoint_host: string;
  call_kind: string;
  status: string;
  error_kind: string;
  http_status: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  cached_tokens: number | null;
  reasoning_tokens: number | null;
  token_source: string;
  requests_count: number;
  attempt_no: number;
  latency_ms: number;
  user_id: number | null;
  topic_id: number | null;
  ref_type: string;
  ref_id: string;
  trace_id: string;
  call_group_id: string;
  extra: Record<string, unknown> | null;
}

export interface UsageLogPage {
  total: number;
  page: number;
  page_size: number;
  items: UsageLogItem[];
}

export interface UsageFilters {
  service: string[];
  feature: string[];
  provider: string[];
  model: string[];
  call_kind: string[];
  status: string[];
}

export interface UsageTraceDetail {
  trace_id: string;
  calls: number;
  total_tokens: number;
  latency_ms: number;
  items: UsageLogItem[];
}

/** Query conditions -- backend param names are snake_case, converted at this layer */
export interface UsageQuery {
  days?: number;
  dayFrom?: string;
  dayTo?: string;
  service?: string;
  feature?: string;
  provider?: string;
  model?: string;
  callKind?: string;
  status?: string;
  userId?: number | '';
  topicId?: number | '';
  traceId?: string;
  q?: string;
  groupBy?: string;
  orderBy?: string;
  limit?: number;
  page?: number;
  pageSize?: number;
}

const PARAM_MAP: Record<keyof UsageQuery, string> = {
  days: 'days', dayFrom: 'day_from', dayTo: 'day_to',
  service: 'service', feature: 'feature', provider: 'provider', model: 'model',
  callKind: 'call_kind', status: 'status', userId: 'user_id', topicId: 'topic_id',
  traceId: 'trace_id', q: 'q', groupBy: 'group_by', orderBy: 'order_by',
  limit: 'limit', page: 'page', pageSize: 'page_size',
};

function qs(query: UsageQuery = {}): string {
  const p = new URLSearchParams();
  (Object.keys(query) as (keyof UsageQuery)[]).forEach((k) => {
    const v = query[k];
    if (v === undefined || v === null || v === '') return;
    p.set(PARAM_MAP[k], String(v));
  });
  const s = p.toString();
  return s ? `?${s}` : '';
}

async function request<T>(path: string, token: string): Promise<T> {
  const resp = await fetch(`/api/admin/llm-usage${path}`, {
    headers: localizedHeaders({ Authorization: `Bearer ${token}` }),
  });
  if (!resp.ok) throw new Error(await readApiError(resp, `请求 ${path} 失败`));
  return resp.json() as Promise<T>;
}

export const adminLlmUsageApi = {
  overview: (token: string, q?: UsageQuery) =>
    request<UsageOverview>(`/overview${qs(q)}`, token),
  trend: (token: string, q?: UsageQuery) =>
    request<UsageTrend>(`/trend${qs(q)}`, token),
  byFeature: (token: string, q?: UsageQuery) =>
    request<UsageFeatureRank>(`/by-feature${qs(q)}`, token),
  logs: (token: string, q?: UsageQuery) =>
    request<UsageLogPage>(`/logs${qs(q)}`, token),
  filters: (token: string) => request<UsageFilters>('/filters', token),
  trace: (token: string, traceId: string) =>
    request<UsageTraceDetail>(`/trace/${encodeURIComponent(traceId)}`, token),
};

// ── Display helpers ────────────────────────────────────────────────────────────────
/** Large numbers like tokens use K/M abbreviations, call counts use thousands separators */
export function fmtCompact(n: number): string {
  if (!Number.isFinite(n)) return '—';
  if (Math.abs(n) >= 1e8) return `${(n / 1e8).toFixed(2)}亿`;
  if (Math.abs(n) >= 1e4) return `${(n / 1e4).toFixed(1)}万`;
  return n.toLocaleString('zh-CN');
}

export function fmtInt(n: number): string {
  return Number.isFinite(n) ? n.toLocaleString('zh-CN') : '—';
}

export const STATUS_LABEL: Record<string, string> = {
  ok: '成功',
  truncated: '输出被截断',
  parse_error: '响应解析失败',
  http_error: '接口报错',
  network_error: '网络未达',
};

export const SERVICE_LABEL: Record<string, string> = {
  backend: '主后端',
  telemetry: '跑批服务',
  sentinel: '舆情服务',
  agent: '对话助手',
  checker: '检测命令行',
};

export const CALL_KIND_LABEL: Record<string, string> = {
  chat: '对话',
  embedding: '向量化',
  image: '图片生成',
  rerank: '重排',
};
