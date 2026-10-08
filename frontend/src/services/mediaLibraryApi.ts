// Media library API -- image overview under the workbench "Content & publishing" group.
// Backend geo/api/admin_media.py, mounted at /api/admin/media, guarded by require_workbench.
// See the page pages/Admin/MediaLibrary.tsx (route /ops/media).

import { localizedHeaders, readApiError } from './apiError';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

export interface MediaItem {
  id: number;
  topic_id: number;
  topic_name: string;
  owner_email: string;
  filename: string;
  kind: string;              // 'image' | 'video'
  mime: string;
  size: number;
  uploaded_at?: string | null;
  /** Public preview URL -- the same URL embedded in the body */
  url: string;
  /** Whether the file is on **this host**. The test site and production share appdb but each has its own disk, so a row existing without the file is normal */
  file_present: boolean;
  /** Asset description (filled in by the user on upload) */
  description: string;
  /** Number of times articles successfully used this user's original image */
  useCount: number;
  // ─── Source and content label ───
  is_ai_generated: boolean;
  gen_model: string;
  gen_trace_no: string;
  has_implicit_mark: boolean;
  /** Whether a visible watermark was drawn at generation time; the list page doesn't read files, so only this value in the details is trustworthy */
  has_explicit_mark: boolean;
  meta: Record<string, string>;
  // ─── External review soft signals ───
  external_status: string;      // ok / degraded / skipped / '' (not submitted)
  external_risk: string;        // high / medium / low / '' (no risk)
  external_labels: string[];
  external_descriptions: string[];
}

export interface MediaList { total: number; items: MediaItem[] }

export interface MediaStats {
  total: number;
  ai_generated: number;
  /** AI-generated but without a content ID -- label missing, needs to be re-applied */
  missing_mark: number;
  /** External review judged high / medium */
  risky: number;
  unscanned: number;
}

export interface MediaFilters {
  topicId?: number;
  kind?: 'image' | 'video' | 'all';
  source?: 'ai' | 'user' | 'all';
  risk?: 'high' | 'medium' | 'low' | 'risky' | 'none' | 'unscanned' | 'all';
  mark?: 'marked' | 'unmarked' | 'all';
  q?: string;
  limit?: number;
  offset?: number;
}

async function request<T>(method: string, path: string, token: string): Promise<T | null> {
  const resp = await fetch(`${API_BASE}/admin/media${path}`, {
    method,
    headers: localizedHeaders({ Authorization: `Bearer ${token}` }),
  });
  if (!resp.ok) throw new Error(await readApiError(resp, `Request ${method} ${path} failed`));
  if (resp.status === 204) return null;
  return resp.json() as Promise<T>;
}

function qs(f: MediaFilters): string {
  const p = new URLSearchParams();
  if (f.topicId) p.set('topic_id', String(f.topicId));
  if (f.kind) p.set('kind', f.kind);
  if (f.source) p.set('source', f.source);
  if (f.risk) p.set('risk', f.risk);
  if (f.mark) p.set('mark', f.mark);
  if (f.q) p.set('q', f.q);
  if (f.limit) p.set('limit', String(f.limit));
  if (f.offset) p.set('offset', String(f.offset));
  const s = p.toString();
  return s ? `?${s}` : '';
}

export const mediaLibraryApi = {
  list: (token: string, f: MediaFilters = {}) => request<MediaList>('GET', qs(f), token),
  stats: (token: string, topicId?: number) =>
    request<MediaStats>('GET', `/stats${topicId ? `?topic_id=${topicId}` : ''}`, token),
  /** Details -- only here is file metadata read; the list page doesn't (reading files per row would drag the list down) */
  get: (id: number, token: string) => request<MediaItem>('GET', `/${id}`, token),
  rescan: (id: number, token: string) => request<MediaItem>('POST', `/${id}/rescan`, token),
  remark: (id: number, token: string) => request<MediaItem>('POST', `/${id}/remark`, token),
  remove: (id: number, token: string) => request<null>('DELETE', `/${id}`, token),
};
