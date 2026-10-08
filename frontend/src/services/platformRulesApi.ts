// Platform review rule library API — /api/admin/platform-rules, guarded by backend require_admin.
// Rule text is injected into the prompt per publish row platform at article generation; "learn from rejections" distills platform rejection reasons
// into rule increments (pending), effective after admin adoption.

import { localizedHeaders, readApiError } from './apiError';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

export interface PlatformRule {
  platform: string;
  rules_text: string;
  pending_rules_text: string;
  learned_at?: string | null;
  updated_at?: string | null;
  rejected_count: number;
  // 2026-08-04 — auto-adapt: in_use = this platform is really being placed on (appears in publish plans/placement tasks/drafted articles);
  // usage = sources of use; ai_draft = the rule is an AI first draft not yet proofread by a human.
  in_use?: boolean;
  usage?: string[];
  ai_draft?: boolean;
}

// Result of one auto-adapt round
export interface PlatformRuleSyncResult {
  in_use: number;
  created: string[];       // Platforms with newly created rows
  seeded: string[];        // Those with auto-generated first drafts taking effect
  learned: string[];       // Those producing increments pending adoption
  seed_failed: string[];
  learn_failed: string[];
  skipped_seed: string[];  // Not enough quota this round; first drafts filled in next round
  rules: PlatformRule[];
}

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
  const resp = await fetch(`${API_BASE}/admin/platform-rules${path}`, init);
  if (!resp.ok) {
    const msg = await readApiError(resp, `Request ${method} ${path} failed`);
    throw new Error(msg);
  }
  return resp.json() as Promise<T>;
}

export const platformRulesApi = {
  async list(token: string): Promise<PlatformRule[]> {
    return request('GET', '', token);
  },
  async update(
    platform: string, rulesText: string, token: string, clearPending = false,
  ): Promise<PlatformRule> {
    return request('PUT', `/${encodeURIComponent(platform)}`, token,
      { rules_text: rulesText, clear_pending: clearPending });
  },
  // LLM generates a first draft (not persisted to DB; returns the draft text for admin proofreading)
  async seed(platform: string, token: string): Promise<{ platform: string; draft: string }> {
    return request('POST', `/${encodeURIComponent(platform)}/seed`, token);
  },
  // All platforms: aggregate rejection reasons -> LLM distills increments written to pending
  async learn(token: string): Promise<PlatformRule[]> {
    return request('POST', '/learn', token);
  },
  async approvePending(platform: string, token: string): Promise<PlatformRule> {
    return request('POST', `/${encodeURIComponent(platform)}/approve-pending`, token);
  },
  // One auto-adapt round: find platforms in use without rules -> create row + AI first draft (effective immediately, marked unproofread);
  // rejection reasons for existing platforms -> distill increments into pending for adoption. Same service as the daily cron.
  async sync(token: string): Promise<PlatformRuleSyncResult> {
    return request('POST', '/sync', token);
  },
  async remove(platform: string, token: string): Promise<{ deleted: string }> {
    return request('DELETE', `/${encodeURIComponent(platform)}`, token);
  },
};
