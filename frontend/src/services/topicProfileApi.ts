// Phase D — user-side topic materials / prompt selection / submit-for-review API client.
//
// Related server routes: backend/geo/api/ai_telemetry.py
//  PUT  /api/ai-telemetry/topics/{id}/profile
//  POST /api/ai-telemetry/topics/{id}/expand-queries
//  POST /api/ai-telemetry/topics/{id}/selected-queries
//  POST /api/ai-telemetry/topics/{id}/submit-for-review

import { localizedHeaders, readApiError } from './apiError';
import type { BrandProfile, SceneType, Topic } from './aiTelemetryApi';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

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

// 2026-05-28 v2 — return structure of the 4-dimension scene expansion.
// Old callers still use .queries (flat array), kept here as a fallback field.
export interface SceneExpansionResult {
  queries: string[];
  model?: string;
  error?: string | null;
}
export interface ExpandQueriesResp {
  seed: string;
  total_count?: number;
  scenes?: Partial<Record<SceneType, SceneExpansionResult>>;
  /** @deprecated 2026-05-28 — old schema field; the new backend returns only scenes; the frontend still reads it for compatibility */
  queries?: string[];
  model?: string;
  // Which product in the materials the seed hit (added 2026-09-22; null / omitted = no hit)
  matched_product?: { name: string; category: string; is_featured: boolean } | null;
}

export interface SelectedItem {
  text: string;
  selected: boolean;
  /** 2026-05-20 — which seed query this query came from; only present at the candidate stage, not backfilled for existing queries. */
  seed?: string;
  /** 2026-05-28 — 4-dimension scene tag; required for newly selected candidates, may be omitted for old options with the backend falling back to search. */
  scene_type?: SceneType;
}

export interface ExtractProfileResp {
  profile: BrandProfile;
  used_model: string;
  // Seed prompt candidates offered by the LLM along the way (may be empty). The frontend appends them to the seed query step in the editor.
  seed_suggestions?: string[];
  // Result of putting the whole original text into the vector store (2026-09-22); null = not persisted (persist=false or ingestion failed; form extraction unaffected)
  // embed_error: reason when not all vectors were generated. "no_provider" = the backend has no embedding key configured; anything else is a vendor error / timeout
  material?: { source: string; chunks: number; embedded: number; embed_error?: string } | null;
}

// A document in the vector store (N chunks of the same source aggregated). Empty topic_id = account-level (passed during the wizard stage)
export interface MaterialDoc {
  source: string;
  topic_id: number | null;
  title: string;
  chunks: number;
  chars: number;
  embedded: number;
  created_at: string | null;
}

// 2026-05-18 — topic media assets (images / videos), the non-text materials in the "Upload materials" modal
export interface TopicMedia {
  id: number;
  topic_id: number;
  filename: string;
  kind: 'image' | 'video';
  mime: string;
  size: number;
  url: string;              // /topics/{id}/media/{mid}/blob from the backend, readable only with Bearer
  public_url?: string | null; // Preferred if the backend provides a public URL; old backends may not
  description?: string;     // Asset description
  useCount: number;         // Number of times articles successfully used this user's original image
  uploaded_at: string;
}

/** Response of GET/PATCH /topics/{id}/schedule -- the Chinese keys of backend services/topic_schedule.describe. */
export interface TopicScheduleInfo {
  topic_id: number;
  topic_name?: string;
  在跑: boolean;
  状态: 'daily' | 'weekly' | 'off' | 'paused' | string;
  节奏: string;
  开跑时间: string;
  提示词数: number;
  引擎数: number;
  每轮问答数: number;
  每次问答单价?: string;
  每轮约扣?: string;
  计费口径?: string;
  怎么停?: string;
  /** The topic's current engine keys (raw, including ops-switched channel variants like qwen_browser; merged via normalizeEngine for display) */
  引擎?: string[];
  /** Effective value of the cycle anchor; custom=false means the platform default is used */
  锚点?: { weekday: number; hour: number; minute: number; 自定义: boolean };
}

export interface SchedulePatch {
  /** daily = one round per day / weekly = one round per week / off = stopped. Omitted = cadence unchanged. */
  schedule_tier?: 'daily' | 'weekly' | 'off';
  /** Temporarily pause N days (1–90), automatically resuming when due with the config preserved. */
  pause_days?: number;
  /** true = end the pause immediately. */
  clear_pause?: boolean;
  /** Which models to run: canonical keys (deepseek/qwen/doubao/wenxin/yuanbao/kimi), at least 1. Omitted = unchanged. */
  engines?: string[];
  /** Weekday to start (0=Monday..6=Sunday, only for the weekly cadence); -1 = back to platform default; omitted = unchanged. */
  cycle_anchor_weekday?: number;
  /** Hour to start (0-23, Beijing time); -1 = back to platform default; omitted = unchanged. */
  cycle_anchor_hour?: number;
}

export const topicProfileApi = {
  async updateProfile(topicId: number, profile: BrandProfile, token: string): Promise<Topic> {
    return request<Topic>('PUT', `/topics/${topicId}/profile`, token, profile);
  },
  /** This topic's continuous monitoring cadence (read-only). */
  async getSchedule(topicId: number, token: string): Promise<TopicScheduleInfo> {
    return request<TopicScheduleInfo>('GET', `/topics/${topicId}/schedule`, token);
  },
  /**
   * Client self-service change of the monitoring cadence.
   * Before this, the only write entry for schedule_tier was the ops console's PATCH /admin/topics/{id}/schedule,
   * with no front-end entry at all -- while page copy and the agent were both telling users to "go to the topic settings page and turn on scheduled audits".
   */
  async patchSchedule(topicId: number, patch: SchedulePatch, token: string): Promise<TopicScheduleInfo> {
    return request<TopicScheduleInfo>('PATCH', `/topics/${topicId}/schedule`, token, patch);
  },
  /**
   * 2026-05-28 v2 — 4-dimension scene expansion.
   * scenes omitted -> the backend runs all 4 dimensions by default; count_per_scene omitted -> the backend defaults to 50 per dimension (200 total).
   * A scene in the response may carry an error field (brand is skipped automatically when target is empty); the frontend shows tabs per scene.
   */
  async expandQueries(
    topicId: number,
    seed: string,
    options: { scenes?: SceneType[]; countPerScene?: number } | undefined,
    token: string,
  ): Promise<ExpandQueriesResp> {
    const body: Record<string, unknown> = { seed };
    if (options?.scenes) body.scenes = options.scenes;
    if (options?.countPerScene) body.count_per_scene = options.countPerScene;
    return request<ExpandQueriesResp>(
      'POST', `/topics/${topicId}/expand-queries`, token, body,
    );
  },
  async updateSelectedQueries(topicId: number, items: SelectedItem[], token: string): Promise<Topic> {
    return request<Topic>(
      'POST', `/topics/${topicId}/selected-queries`, token, { items },
    );
  },
  /** Soft delete / restore prompts. Deleted queries no longer join batch runs, report metrics or publish query selection, but the queries themselves and
   *  historical responses are kept (restorable any time). rebuildHistory=true also recomputes historical reports under the new metric definition --
   *  default false: historical numbers in delivered reports stay unchanged, only the future is affected. */
  async deleteQueries(
    topicId: number,
    items: { text: string; deleted: boolean }[],
    token: string,
    rebuildHistory = false,
  ): Promise<Topic> {
    return request<Topic>(
      'POST', `/topics/${topicId}/delete-queries`, token,
      { items, rebuild_history: rebuildHistory },
    );
  },
  // topicId set -> the original text is attached to that topic; absent (wizard stage) -> attached at the account level
  async extractProfile(text: string, token: string, topicId?: number): Promise<ExtractProfileResp> {
    return request<ExtractProfileResp>('POST', '/profile/extract', token, { text, topic_id: topicId ?? null });
  },
  // PDF / Word / plain text files — the backend parses them with pypdf / python-docx and runs the same
  // LLM pipeline, so the frontend needs neither PDF.js nor mammoth, keeping the bundle lean.
  async extractProfileFile(file: File, token: string, topicId?: number): Promise<ExtractProfileResp> {
    const fd = new FormData();
    fd.append('file', file, file.name);
    if (topicId != null) fd.append('topic_id', String(topicId));
    const resp = await fetch(`${API_BASE}/ai-telemetry/profile/extract-file`, {
      method: 'POST',
      headers: localizedHeaders({ Authorization: `Bearer ${token}` }),
      body: fd,
    });
    if (!resp.ok) {
      const msg = await readApiError(resp, 'AI 解析失败');
      throw new Error(msg);
    }
    return resp.json();
  },

  async listMaterials(topicId: number, token: string): Promise<MaterialDoc[]> {
    return request<MaterialDoc[]>('GET', `/topics/${topicId}/materials`, token);
  },
  async deleteMaterial(topicId: number, source: string, scope: 'topic' | 'account', token: string): Promise<void> {
    await request<unknown>(
      'DELETE', `/topics/${topicId}/materials?source=${encodeURIComponent(source)}&scope=${scope}`, token,
    );
  },

  async listMedia(topicId: number, token: string): Promise<TopicMedia[]> {
    return request<TopicMedia[]>('GET', `/topics/${topicId}/media`, token);
  },
  async getMediaBlob(topicId: number, mediaId: number, token: string): Promise<Blob> {
    const resp = await fetch(`${API_BASE}/ai-telemetry/topics/${topicId}/media/${mediaId}/blob`, {
      headers: localizedHeaders({ Authorization: `Bearer ${token}` }),
    });
    if (!resp.ok) {
      const msg = await readApiError(resp, '图片加载失败');
      throw new Error(msg);
    }
    return resp.blob();
  },
  async uploadMedia(topicId: number, file: File, token: string, description = ''): Promise<TopicMedia> {
    const fd = new FormData();
    fd.append('file', file, file.name);
    if (description.trim()) fd.append('description', description.trim());
    const resp = await fetch(`${API_BASE}/ai-telemetry/topics/${topicId}/media`, {
      method: 'POST',
      headers: localizedHeaders({ Authorization: `Bearer ${token}` }),
      body: fd,
    });
    if (!resp.ok) {
      const msg = await readApiError(resp, '上传失败');
      throw new Error(msg);
    }
    return resp.json();
  },
  async updateMedia(topicId: number, mediaId: number, description: string, token: string): Promise<TopicMedia> {
    return request<TopicMedia>('PATCH', `/topics/${topicId}/media/${mediaId}`, token, { description });
  },
  async deleteMedia(topicId: number, mediaId: number, token: string): Promise<void> {
    await request<void>('DELETE', `/topics/${topicId}/media/${mediaId}`, token);
  },
};
