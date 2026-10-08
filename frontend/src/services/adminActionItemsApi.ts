// Flywheel P2 — optimization task center API (/api/admin/action-items, guarded by require_workbench).
// Producers are on the telemetry side (daily report suggestions/retest regressions/insight suggestions written via side paths); this is the consumer.
import { localizedHeaders, readApiError } from './apiError';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

// self=true uses the user self-service channel (/api/action-items, guarded by ownership); otherwise the admin platform-wide channel.
async function request<T>(
  method: string, path: string, token: string, body?: unknown, self = false,
): Promise<T> {
  const init: RequestInit = {
    method,
    headers: localizedHeaders({
      Authorization: `Bearer ${token}`,
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    }),
  };
  if (body !== undefined) init.body = JSON.stringify(body);
  const prefix = self ? '/action-items' : '/admin/action-items';
  const resp = await fetch(`${API_BASE}${prefix}${path}`, init);
  if (!resp.ok) throw new Error(await readApiError(resp, `Request ${method} ${path} failed`));
  if (resp.status === 204) return undefined as T;
  return resp.json() as Promise<T>;
}

export type ActionItemStatus = 'open' | 'converted' | 'dismissed' | 'done';
export type ActionItemSource =
  | 'daily_brief' | 'retest' | 'cell_insight' | 'source_strategy' | 'manual';

export interface ActionItem {
  id: number;
  topic_id: number;
  source: ActionItemSource;
  priority: string;                 // P0/P1/P2
  title: string;
  body_md: string;
  suggested_action: string;         // add_plan_item/retest/fix_content/other
  status: ActionItemStatus;
  source_ref: Record<string, unknown>;
  linked_plan_item_id?: string | null;
  linked_doc_id?: number | null;
  created_at: string;
  resolved_at?: string | null;
}

export const adminActionItemsApi = {
  // self=true: on the "Optimization tasks" page clients only operate on tasks of their own topics (after the workbench opened externally).
  async list(
    topicId: number, token: string, status?: ActionItemStatus, self = false,
  ): Promise<ActionItem[]> {
    const qs = status ? `?status=${status}` : '';
    return request('GET', `/topics/${topicId}${qs}`, token, undefined, self);
  },
  async dismiss(itemId: number, token: string, self = false): Promise<ActionItem> {
    return request('POST', `/${itemId}/dismiss`, token, undefined, self);
  },
  async convert(
    itemId: number, token: string,
    payload: { action: 'add_plan_item' | 'retest'; seed?: string; queries?: string[] },
    self = false,
  ): Promise<ActionItem> {
    return request('POST', `/${itemId}/convert`, token, payload, self);
  },
};
