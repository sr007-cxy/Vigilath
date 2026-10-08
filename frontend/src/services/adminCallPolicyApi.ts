// Call management (admin-only) — /api/admin/call-policies
// Backend geo/api/admin_call_policy.py. Internal (web/ops console/platform automation) and external
// (open API/embed tokens/instant messaging/skill packs) use the same tiers and the same call record table.
import { readApiError, localizedHeaders } from './apiError';

export interface CallPolicy {
  key: string;
  label: string;
  channel: string;
  allow_actions: string;
  deny_actions: string;
  exec_mode: string;        // auto | api | any
  run_mode: string;         // immediate | queue | any
  engines: string;
  daily_runs: number;
  rpm: number;
  max_concurrent: number;
  priority: number;
  unlimited: number;
  enabled: number;
  note: string;
  is_internal: boolean;
  updated_at: string | null;
}

export interface PolicyListOut {
  policies: CallPolicy[];
  channels: string[];
  exec_modes: string[];
  run_modes: string[];
}

export interface AuditItem {
  id: number;
  ts: string | null;
  bj_date: string;
  channel: string;
  policy_key: string;
  tenant_id: number | null;
  tid: string;
  account_id: number | null;
  endpoint: string;
  action: string;
  topic_id: number | null;
  engine: string;
  exec_mode: string;
  run_mode: string;
  decision: string;
  reason: string;
  cells: number;
}

export interface AuditPage { total: number; items: AuditItem[] }

export interface AuditSummary {
  days: number;
  by_channel: { channel: string; calls: number; cells: number; denied: number }[];
}

// Localized labels are defined here in one place, pages don't assemble their own -- no English code symbols appear in the admin UI
export const CHANNEL_LABEL: Record<string, string> = {
  web: '内部 · 网页',
  admin: '内部 · 运营台',
  cron: '内部 · 平台自动化',
  api: '对外 · 开放接口',
  embed: '对外 · 嵌入令牌',
  im: '对外 · 即时通讯',
  skill: '对外 · 技能包',
};

export const EXEC_LABEL: Record<string, string> = {
  auto: '走自动化(浏览器账号池)',
  api: '走接口直连',
  any: '不限',
};

export const RUN_LABEL: Record<string, string> = {
  immediate: '立刻执行',
  queue: '进调度排队',
  any: '不限',
};

export const DECISION_LABEL: Record<string, string> = {
  allow: '放行',
  deny: '拦截',
};

const BASE = '/api/admin/call-policies';

async function request<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    ...init,
    headers: localizedHeaders({
      Authorization: `Bearer ${token}`,
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
    }),
  });
  if (!resp.ok) throw new Error(await readApiError(resp, `请求 ${path} 失败`));
  return resp.json() as Promise<T>;
}

export const adminCallPolicyApi = {
  list: (token: string) => request<PolicyListOut>('', token),
  update: (token: string, key: string, patch: Partial<CallPolicy>) =>
    request<CallPolicy>(`/${encodeURIComponent(key)}`, token, {
      method: 'PUT', body: JSON.stringify(patch),
    }),
  create: (token: string, body: Partial<CallPolicy>) =>
    request<CallPolicy>('', token, { method: 'POST', body: JSON.stringify(body) }),
  bind: (token: string, body: { policy_key: string; tid?: string; tenant_id?: number }) =>
    request<Record<string, unknown>>('/bind', token, {
      method: 'POST', body: JSON.stringify(body),
    }),
  audit: (token: string, q: { channel?: string; policy_key?: string; decision?: string;
                              days?: number; limit?: number; offset?: number } = {}) => {
    const p = new URLSearchParams();
    Object.entries(q).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') p.set(k, String(v));
    });
    const s = p.toString();
    return request<AuditPage>(`/audit${s ? `?${s}` : ''}`, token);
  },
  summary: (token: string, days = 7) => request<AuditSummary>(`/audit/summary?days=${days}`, token),
};
