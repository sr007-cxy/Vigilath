// Tenant portal (role=tenant) — /api/gateway-portal
// tenant_id is force-injected by the backend from the logged-in account's binding; the frontend doesn't send it.
import { readApiError, localizedHeaders } from './apiError';

export interface PortalEngineUsage {
  engine: string;
  daily_quota: number;
  used_today: number;
  remaining: number;
}

export interface PortalOverview {
  tenant: { id: number; name: string; status: string; tier: string; credit_balance: number };
  engines: PortalEngineUsage[];
  billable_total: number;
  credits_spent_total: number;
}

export interface PortalJob {
  id: number;
  tenant_id: string | null;
  engine: string;
  query: string;
  status: string;
  error: string | null;
  answer?: string;
  citations?: { url?: string; domain?: string; title?: string }[];
  source_url?: string | null;
  video_url?: string | null;
  created_at: string | null;
  finished_at: string | null;
}

async function request<T>(path: string, token: string): Promise<T> {
  const resp = await fetch(`/api/gateway-portal${path}`, {
    headers: localizedHeaders({ Authorization: `Bearer ${token}` }),
  });
  if (!resp.ok) {
    throw new Error(await readApiError(resp, `Request ${path} failed`));
  }
  return resp.json() as Promise<T>;
}

export const gatewayPortalApi = {
  async overview(token: string): Promise<PortalOverview> {
    return request('/overview', token);
  },
  async jobs(
    token: string,
    opts: { limit?: number; offset?: number; engine?: string; status?: string; since?: string; until?: string } = {},
  ): Promise<{ jobs: PortalJob[]; total: number; limit: number; offset: number }> {
    const p = new URLSearchParams();
    p.set('limit', String(opts.limit ?? 50));
    p.set('offset', String(opts.offset ?? 0));
    if (opts.engine) p.set('engine', opts.engine);
    if (opts.status) p.set('status', opts.status);
    if (opts.since) p.set('since', opts.since);
    if (opts.until) p.set('until', opts.until);
    return request(`/jobs?${p.toString()}`, token);
  },
};
