// Self-built publishing account pool API -- /api/publish-dispatch/*, /api/publish-accounts/*.
// Guarded by backend require_roles("admin", "operator") (geo/main.py:117).
//
// Follows the request/requestAt shape of contentApi.ts:153-177: token is the last required positional parameter,
// errors uniformly go through readApiError (turning the backend detail/message into plain language rather than throwing "500").
//
// 🔴 This file covers **one of the three channels** (server_pool). The other two in the unified publish drawer:
//   aggregator -> retired
//   plugin     -> services/wechatSyncPlugin (ops' local browser, not via the backend)
// Don't try to fold them in here -- their account identities come from different sources and their executors differ; merging would break
// the (doc_id, platform, account_db_id) idempotency unique key, showing up as the same draft being published twice on the same account
// (see the channel comment in backend/geo/models/publish_accounts.py).

import { localizedHeaders, readApiError } from './apiError';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

/** Channel. An account belongs to exactly one, immutable after creation (backend ALL_CHANNELS). */
export type PublishChannel = 'server_pool' | 'aggregator' | 'plugin';

/** Render precheck of a draft on a platform -- the same render_for_publish used at claim time. */
export interface PublishPreflight {
  /** Non-empty = **can't publish** to this platform. 🔴 The frontend must make the row uncheckable accordingly. */
  reject_reason: string | null;
  warnings: string[];
  title_len: number;
  body_len: number;
  body_format: string;
  images: number;
  has_cover: boolean;
}

/** A row of GET /publish-dispatch/targets = one selectable publish target (one account). */
export interface PublishTargetRow {
  account_db_id: number;
  channel: PublishChannel;
  platform: string;
  platform_name: string;
  platform_enabled: boolean;
  account_handle: string;
  /**
   * How the channel's executor identifies this account -- **use it for submission, account_handle for display**.
   * null for server_pool (the executor is our worker, and tasks pass account_db_id);
   * for aggregator it's the Aitoearn-side account_id.
   */
  channel_ref: string | null;
  effective_status: string;
  /** Can it publish right now. For server_pool this flag shares its source with claim's in-lock criterion (account_schedulable). */
  schedulable: boolean;
  /** Plain-language reason it can't publish; null when schedulable is true. */
  unschedulable_reason: string | null;
  /** Whether it can be submitted to the waiting queue; temporary states like busy, cooling down and rate-limited are still true. */
  queueable: boolean;
  /** Hard reason it can't be queued; null when queueable is true. */
  queue_block_reason: string | null;
  /** null = unlimited */
  remaining_today: number | null;
  daily_cap: number | null;
  preflight: PublishPreflight;
}

export interface PublishTargetsResp {
  doc: { id: number; title: string; status: string; markdown_len: number };
  targets: PublishTargetRow[];
  channels_covered: PublishChannel[];
  plugin_note: string;
}

/** Per-account result of POST /publish-dispatch/tasks. action is **each account's own** outcome. */
export interface DispatchCreateResult {
  account_db_id: number;
  task_id?: number;
  dispatch_id?: number;
  action: 'created' | 'reused' | 'reused_task' | 'skip';
  detail: string;
}

export interface DispatchCreateResp {
  doc_id: number;
  results: DispatchCreateResult[];
}

export interface PublishPlatformCapability {
  code: string;
  name: string;
  channel: PublishChannel;
  enabled: boolean;
  password_login_supported: boolean;
  web_login_supported: boolean;
  publish_supported?: boolean;
  claimable?: boolean;
  support_note?: string | null;
}

export interface PublishAccountRow {
  id: number;
  platform: string;
  channel: PublishChannel;
  account_handle: string | null;
  status: string;
  effective_status: string;
  credential_owner: string;
  auth_type: string | null;
  proxy_id: number | null;
  egress: 'proxy' | 'host' | null;
  owner_worker_id: number | null;
  exit_ip: string | null;
  expires_at: string | null;
  warmup_daily_cap: number | null;
  last_used_at: string | null;
  last_fail_type: string | null;
  last_fail_reason: string | null;
  last_fail_at: string | null;
  used_today: number;
  daily_cap: number | null;
  cooldown_minutes: number | null;
  effective_cooldown_minutes: number;
  effective_cap: number;
  remaining: number | null;
  rate_limited_until: string | null;
}

export interface PublishAccountPage {
  items: PublishAccountRow[];
  total: number;
  page: number;
  page_size: number;
}

export interface PublishPoolStatus {
  platform: string;
  total: number;
  schedulable: number;
  warming: number;
  rate_limited: number;
  quarantined: number;
  unschedulable: number;
  expiring_soon: number;
  remaining_today: number | null;
}

export interface PublishProxyOption {
  id: number;
  label: string;
  disabled: boolean;
  last_exit_ip?: string | null;
}

export type PublishQueueStatus =
  | 'pending' | 'running' | 'pending_url' | 'published'
  | 'draft_saved' | 'url_submitted' | 'failed' | 'abandoned' | 'cancelled';

export interface PublishQueueRow {
  dispatch_id: number;
  task_id: number;
  doc_id: number;
  doc_title: string;
  platform: string;
  platform_name: string;
  account_db_id: number;
  account_handle: string | null;
  status: PublishQueueStatus;
  dispatch_status: string;
  business_status: string;
  planned_publish_at: string | null;
  created_at: string;
  claimed_at: string | null;
  finished_at: string | null;
  last_error: string | null;
  link_type: 'published' | 'candidate' | 'draft' | null;
  link_url: string | null;
  draft_id?: string | null;
  stuck_candidate?: boolean;
}

export interface PublishQueuePage {
  items: PublishQueueRow[];
  total: number;
  page: number;
  page_size: number;
  date: string;
  claim_stale_minutes?: number;
}

export interface PublishPasswordLoginPayload {
  platform: string;
  identifier: string;
  password: string;
  proxy_id?: number;
  auto_proxy?: boolean;
  egress?: 'proxy' | 'host';
  credential_owner?: string;
  warmup_enabled?: boolean;
  warmup_days?: number;
  warmup_daily_cap?: number;
  daily_cap?: number;
  account_db_id?: number;
}

export interface PublishWebLoginPayload {
  platform: string;
  proxy_id?: number;
  auto_proxy?: boolean;
  egress?: 'proxy' | 'host';
  credential_owner?: string;
  warmup_enabled?: boolean;
  warmup_days?: number;
  warmup_daily_cap?: number;
  daily_cap?: number;
  account_db_id?: number;
}

export interface PublishPluginSessionImportPayload {
  platform: string;
  storage_state: { cookies?: unknown[]; origins?: unknown[] };
  credential_owner?: string;
  warmup_enabled?: boolean;
  warmup_days?: number;
  warmup_daily_cap?: number;
  daily_cap?: number;
  proxy_id?: number;
  auto_proxy?: boolean;
  egress?: 'proxy' | 'host';
  account_db_id?: number;
  account_handle?: string;
  user_agent?: string;
  ua_platform?: string;
  account_key?: string;
}

export interface PublishWxgzhOfficialApiPayload {
  appid: string;
  appsecret: string;
  credential_owner?: string;
  warmup_enabled?: boolean;
  warmup_days?: number;
  warmup_daily_cap?: number;
  daily_cap?: number;
  egress?: 'proxy' | 'host';
  proxy_id?: number;
  auto_proxy?: boolean;
}

export interface PublishManualLoginState {
  manual_required: true;
  manual_session_id: string;
  qr_image: string | null;
  preview_image: string | null;
  interactive: boolean;
  expires_in: number;
  proxy_id?: number | null;
  detail?: string;
}

export type PublishPasswordLoginResult =
  | { renewed: boolean; account: PublishAccountRow; manual_required?: false }
  | PublishManualLoginState;

async function request<T>(
  method: string, path: string, token: string, body?: unknown,
): Promise<T> {
  const url = `${API_BASE}${path}`;
  const init: RequestInit = {
    method,
    headers: localizedHeaders({
      Authorization: `Bearer ${token}`,
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    }),
  };
  if (body !== undefined) init.body = JSON.stringify(body);
  let resp: Response;
  try {
    resp = await fetch(url, init);
  } catch (e) {
    const reason = e instanceof Error ? e.message : String(e);
    throw new Error(`账号池接口请求失败：无法连接后端 ${method} ${url}。请确认前端 VITE_API_URL 指向正确后端，且后端服务已启动。原始错误：${reason}`);
  }
  if (!resp.ok) {
    const msg = await readApiError(resp, `Request ${method} ${path} failed`);
    throw new Error(describePublishPoolError(method, path, url, resp.status, msg));
  }
  if (resp.status === 204) return undefined as T;
  return resp.json() as Promise<T>;
}

function describePublishPoolError(
  method: string,
  path: string,
  url: string,
  status: number,
  rawMessage: string,
): string {
  const raw = rawMessage || `HTTP ${status}`;
  if (path === '/publish-accounts/plugin-session/import') {
    if (status === 404 || /^not found$/i.test(raw.trim())) {
      return `导入登录态失败：后端没有找到账号池插件导入接口 ${method} ${url}。请重启后端，确认已加载 /api/publish-accounts/plugin-session/import 路由。原始错误：${raw}`;
    }
    return `导入登录态失败：账号池后端拒绝保存插件登录态（HTTP ${status}）。${raw}`;
  }
  if (path === '/publish-accounts/wxgzh-official-api/import') {
    if (status === 404 || /^not found$/i.test(raw.trim())) {
      return `公众号官方 API 添加失败：后端没有找到接口 ${method} ${url}。请重启后端，确认已加载 /api/publish-accounts/wxgzh-official-api/import 路由。原始错误：${raw}`;
    }
    return `公众号官方 API 添加失败：账号池后端拒绝保存账号（HTTP ${status}）。${raw}`;
  }
  if (status === 404 || /^not found$/i.test(raw.trim())) {
    return `账号池接口不存在或后端路由未加载：${method} ${url} 返回 404。原始错误：${raw}`;
  }
  return raw;
}

export const publishPoolApi = {
  async platforms(token: string): Promise<{ items: PublishPlatformCapability[] }> {
    return request('GET', '/publish-accounts/platforms', token);
  },

  async accounts(
    token: string,
    opts: { platform?: string; status?: string; anomaly?: 'banned' | 'offline'; page?: number; pageSize?: number } = {},
  ): Promise<PublishAccountPage> {
    const qs = new URLSearchParams({
      page: String(opts.page ?? 1), page_size: String(opts.pageSize ?? 20),
      channel: 'server_pool',
    });
    if (opts.platform) qs.set('platform', opts.platform);
    if (opts.status) qs.set('status', opts.status);
    if (opts.anomaly) qs.set('anomaly', opts.anomaly);
    return request('GET', `/publish-accounts?${qs.toString()}`, token);
  },

  async poolStatus(token: string): Promise<PublishPoolStatus[]> {
    return request('GET', '/publish-accounts/pool-status', token);
  },

  async dispatchTasks(
    token: string,
    opts: { platform?: string; status?: PublishQueueStatus; page?: number; pageSize?: number } = {},
  ): Promise<PublishQueuePage> {
    const qs = new URLSearchParams({
      page: String(opts.page ?? 1), page_size: String(opts.pageSize ?? 20),
    });
    if (opts.platform) qs.set('platform', opts.platform);
    if (opts.status) qs.set('status', opts.status);
    return request('GET', `/publish-dispatch/tasks?${qs.toString()}`, token);
  },

  async resolveStuckDispatch(
    token: string,
    dispatchId: number,
    payload: { reason: string; confirmed_not_published?: boolean },
  ): Promise<{ ok: boolean; resolved: boolean; dispatch_status: string; account_status?: string; notes: string[] }> {
    return request('POST', `/publish-dispatch/tasks/${dispatchId}/resolve-stuck`, token, payload);
  },

  async passwordLogin(
    token: string,
    payload: PublishPasswordLoginPayload,
  ): Promise<PublishPasswordLoginResult> {
    return request('POST', '/publish-accounts/password-login', token, payload);
  },

  async webLogin(
    token: string,
    payload: PublishWebLoginPayload,
  ): Promise<PublishManualLoginState> {
    return request('POST', '/publish-accounts/web-login', token, payload);
  },

  async importPluginSession(
    token: string,
    payload: PublishPluginSessionImportPayload,
  ): Promise<{ renewed: boolean; identity_resolved: boolean; account: PublishAccountRow }> {
    return request('POST', '/publish-accounts/plugin-session/import', token, payload);
  },

  async importWxgzhOfficialApi(
    token: string,
    payload: PublishWxgzhOfficialApiPayload,
  ): Promise<{ renewed: boolean; account: PublishAccountRow }> {
    return request('POST', '/publish-accounts/wxgzh-official-api/import', token, payload);
  },

  async pollWebLogin(
    token: string, manualSessionId: string, payload: PublishWebLoginPayload,
  ): Promise<({ status: 'pending'; qr_image: string | null; preview_image: string | null; interactive: boolean; expires_in: number }
    | { status: 'done'; renewed: boolean; account: PublishAccountRow })> {
    return request('POST', '/publish-accounts/web-login/poll', token, {
      ...payload, manual_session_id: manualSessionId,
    });
  },

  async cancelWebLogin(token: string, manualSessionId: string): Promise<{ ok: boolean }> {
    return request('POST', '/publish-accounts/web-login/cancel', token, {
      manual_session_id: manualSessionId,
    });
  },

  async pollManualLogin(
    token: string, manualSessionId: string, payload: PublishPasswordLoginPayload,
  ): Promise<({ status: 'pending'; qr_image: string | null; preview_image: string | null; interactive: boolean; expires_in: number }
    | { status: 'done'; renewed: boolean; account: PublishAccountRow })> {
    return request('POST', '/publish-accounts/password-login/manual-poll', token, {
      ...payload, manual_session_id: manualSessionId,
    });
  },

  async cancelManualLogin(token: string, manualSessionId: string): Promise<{ ok: boolean }> {
    return request('POST', '/publish-accounts/password-login/manual-cancel', token, {
      manual_session_id: manualSessionId,
    });
  },

  async retireAccount(token: string, accountId: number): Promise<{ ok: boolean; cancelled: number }> {
    return request('DELETE', `/publish-accounts/${accountId}`, token);
  },

  async updateAccountSchedule(
    token: string,
    accountId: number,
    payload: { daily_cap?: number; reset_daily_cap?: boolean; cooldown_minutes?: number; reason: string },
  ): Promise<{ ok: boolean; account: PublishAccountRow }> {
    return request('PATCH', `/publish-accounts/${accountId}/schedule`, token, payload);
  },

  async rebindWorker(
    token: string,
    accountId: number,
    payload: { owner_worker_id: number; proxy_id?: number; auto_proxy?: boolean; egress: 'proxy' | 'host'; reason: string },
  ): Promise<{ ok: boolean; account: PublishAccountRow }> {
    return request('POST', `/publish-accounts/${accountId}/rebind-worker`, token, payload);
  },

  async reinstateAccount(
    token: string, accountId: number, reason: string,
  ): Promise<{ ok: boolean; account: PublishAccountRow }> {
    const qs = new URLSearchParams({ reason });
    return request('POST', `/publish-accounts/${accountId}/reinstate?${qs.toString()}`, token);
  },

  async proxies(token: string): Promise<{ items: PublishProxyOption[] }> {
    return request('GET', '/ai-telemetry/admin/proxies', token);
  },

  /**
   * One draft -> all publish targets visible to the backend (server_pool + aggregator, per account).
   *
   * ⚠️ Excludes the plugin channel -- those accounts exist only in the ops machine's browser plugin, invisible to the backend.
   * Callers must merge the results of wechatSyncPlugin.readPluginAccounts() themselves.
   */
  async targets(docId: number, token: string, platforms?: string[]): Promise<PublishTargetsResp> {
    const qs = new URLSearchParams({ doc_id: String(docId) });
    if (platforms?.length) qs.set('platforms', platforms.join(','));
    return request('GET', `/publish-dispatch/targets?${qs.toString()}`, token);
  },

  /**
   * One draft × N server_pool accounts -> N **independent** tasks (F8: partial success and partial failure allowed).
   *
   * 🔴 Only accepts server_pool accounts: other channels' account_db_ids get `action: 'skip'`
   * rather than an error. That guard is part of the idempotency unique constraint, not a bug -- don't change it for the sake of a "unified entry".
   */
  async createDispatchTasks(
    docId: number,
    accountDbIds: number[],
    token: string,
    plannedPublishAt?: string,
  ): Promise<DispatchCreateResp> {
    return request('POST', '/publish-dispatch/tasks', token, {
      doc_id: docId,
      account_db_ids: accountDbIds,
      ...(plannedPublishAt ? { planned_publish_at: plannedPublishAt } : {}),
    });
  },
};
