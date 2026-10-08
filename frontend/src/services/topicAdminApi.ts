// Admin-only topic settings -- stored in the admin section of the profile materials, invisible and unmodifiable on the client side.
// A separate service file (not merged into aiTelemetryApi.ts): this is ops-side configuration, kept apart from the client self-service
// topic CRUD so the permission boundary is obvious at a glance.

import { localizedHeaders, readApiError } from './apiError';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

export interface FixedDenominator {
  topic_id: number;
  /** Configured response denominator (pinned, denom = this value, independent of actual runs); null = not set, computed from actual runs throughout */
  fixed_response_count: number | null;
  /** The share-of-voice numerator counts only category query hits (brand queries excluded) */
  mention_cat_scope: boolean;
  /** Reference value: current prompt count × engine count = theoretical full coverage, for comparison when entering a number */
  query_count: number;
  engine_count: number;
  full_coverage: number;
  /** Full coverage under the category query metric definition (the exposure rate denominator's dimension; citation rate uses the full query list definition above) */
  cat_query_count: number;
  cat_full_coverage: number;
  /** Median of actually run category query combinations over the last 4 closed ISO weeks -- set lower than this and the numerator overflows, clamped to 100% */
  recent_cat_valid: number;
}

async function call<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE}/ai-telemetry${path}`, {
    ...init,
    headers: localizedHeaders({
      Authorization: `Bearer ${token}`,
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
    }),
  });
  if (!resp.ok) throw new Error(await readApiError(resp, `Request ${path} failed`));
  return resp.json() as Promise<T>;
}

export const topicAdminApi = {
  getFixedDenominator: (topicId: number, token: string) =>
    call<FixedDenominator>(`/topics/${topicId}/fixed-denominator`, token),

  /** Both switches are "omitted = untouched": to change only one, pass undefined for the other.
   *  fixed_response_count = null / 0 -> clear the setting, back to using the actual response count */
  setAdminSettings: (
    topicId: number, token: string,
    patch: { fixed_response_count?: number | null; mention_cat_scope?: boolean },
  ) =>
    call<FixedDenominator>(`/topics/${topicId}/fixed-denominator`, token, {
      method: 'PUT',
      body: JSON.stringify(patch),
    }),
};
