// Admin 审核 API — Phase C 单条审核 + Phase D 整张申请审核.
// 端点都挂在 /api/admin/review,后端用 require_admin 守门;调用方需带 admin token.

import { localizedHeaders, readApiError } from './apiError';
import type { BrandProfile, SubmissionStatus, Topic } from './aiTelemetryApi';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

export interface PendingSeedItem {
  topic_id: number;
  topic_name: string;
  target: string;
  user_id: number;
  user_email: string;
  idx: number;
  text: string;
  submitted_at?: string | null;
}

export interface PendingQueryItem {
  topic_id: number;
  topic_name: string;
  target: string;
  user_id: number;
  user_email: string;
  idx: number;
  text: string;
  cluster_id?: number | null;
  submitted_at?: string | null;
}

export interface PendingReview {
  seed_prompts: PendingSeedItem[];
  queries: PendingQueryItem[];
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
  const resp = await fetch(`${API_BASE}/admin/review${path}`, init);
  if (!resp.ok) {
    const msg = await readApiError(resp, `Request ${method} ${path} failed`);
    throw new Error(msg);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json() as Promise<T>;
}

// ── Phase D — 整张申请审核 ───────────────────────────

export interface TopicReviewListItem {
  topic_id: number;
  topic_name: string;
  user_id: number;
  user_email: string;
  submission_status: SubmissionStatus;
  submitted_at?: string | null;
  approved_at?: string | null;
  rejected_at?: string | null;
  profile_name: string;
  company_short_name: string;
  industry: string;
  seed_count: number;
  selected_query_count: number;
  version?: number;
}

export interface TopicChangelogEntry {
  at: string;
  actor_id?: number | null;
  actor_role: 'user' | 'admin' | 'system';
  field: string;
  before?: string | null;
  after?: string | null;
  note?: string | null;
  version?: number | null;
}

export interface ExpansionLogEntry {
  at: string;
  seed: string;
  model: string;
  expanded_count: number;
  raw_excerpt: string;
}

export interface TopicReviewDetail extends Topic {
  user_email: string;
  topic_changelog: TopicChangelogEntry[];
  expansion_log: ExpansionLogEntry[];
}

export interface AdminPatchTopicPayload {
  profile?: BrandProfile;
  seed_prompts?: string[];
  selected_query_texts?: string[];
  add_queries?: string[];
  note?: string;
}

export interface TopicProgressCell {
  query: string;
  engine: string;
  status: 'pending' | 'running' | 'done';
  hit?: boolean | null;
  last_checked_at?: string | null;
}

export interface PublishPlanItem {
  day: number;
  publish_date: string;
  query: string;
  coverage_pct: number;
  priority: 'high' | 'med' | 'low';
  doc_id?: number | null;
  doc_status?: 'draft' | 'pending_review' | 'approved' | 'rejected' | 'published' | null;
  suggested_platforms: string[];
}

export interface ExecutionPlan {
  id: number;
  topic_id: number;
  generated_at: string;
  generated_by_reviewer_id?: number | null;
  status: 'generating' | 'ready' | 'failed';
  error?: string | null;
  overview: Record<string, unknown>;
  topic_changelog: TopicChangelogEntry[];
  expansion_log: ExpansionLogEntry[];
  monitored_queries: string[];
  run_id?: number | null;
  run_status?: 'running' | 'success' | 'failed' | null;
  progress: TopicProgressCell[];
  progress_done: number;
  progress_total: number;
  publishing_plan: PublishPlanItem[];
}

// ── v3.3 战略方案 ───────────────────────────

export type SolutionStatus = 'idle' | 'generating' | 'ready' | 'failed';
export type SolutionSeverity = 'high' | 'med' | 'low';

export interface SolutionDiagnosisCheck {
  category: string;
  status: 'PASS' | 'WARN' | 'FAIL' | 'INFO' | string;
  message: string;
  fix?: string | null;
}

export interface SolutionDiagnosisCluster {
  key: string;
  title_zh: string;
  severity: SolutionSeverity;
  summary: string;
  bullets: string[];
  checks: SolutionDiagnosisCheck[];
}

export interface SolutionExecutionLayer {
  key: string;
  title_zh: string;
  hint: string;
  clusters: string[];
}

export interface SolutionDiagnosis {
  url: string;
  score: number;
  grade: string;
  pass_count: number;
  warn_count: number;
  fail_count: number;
  info_count: number;
  clusters: SolutionDiagnosisCluster[];
  execution_layers: SolutionExecutionLayer[];
  all_checks: SolutionDiagnosisCheck[];
}

export interface SolutionSevenStep {
  step: number;
  name: string;
  core_goal: string;
  core_action: string;
  output_value: string;
}

export interface SolutionKeywordTier {
  tier: string;
  title_zh: string;
  description: string;
  keywords: string[];
}

export interface SolutionVisionItem {
  title: string;
  body: string;
}

export interface TopicStrategicSolution {
  id: number;
  topic_id: number;
  status: SolutionStatus;
  website_url: string;
  error?: string | null;
  generated_by_admin_id?: number | null;
  llm_model: string;
  created_at?: string | null;
  updated_at?: string | null;
  brand_snapshot?: BrandProfile | null;
  diagnosis?: SolutionDiagnosis | null;
  seven_steps: SolutionSevenStep[];
  keyword_tiers: SolutionKeywordTier[];
  vision: SolutionVisionItem[];
}

export const adminReviewApi = {
  // Phase C 兼容入口
  async listPending(token: string): Promise<PendingReview> {
    return request<PendingReview>('GET', '/pending', token);
  },
  async approveSeed(topicId: number, idx: number, token: string): Promise<void> {
    return request<void>('POST', `/seed/${topicId}/${idx}/approve`, token);
  },
  async rejectSeed(topicId: number, idx: number, token: string): Promise<void> {
    return request<void>('POST', `/seed/${topicId}/${idx}/reject`, token);
  },
  async approveQueries(topicId: number, indices: number[], token: string): Promise<void> {
    return request<void>('POST', `/queries/${topicId}/approve`, token, { indices });
  },
  async rejectQueries(topicId: number, indices: number[], token: string): Promise<void> {
    return request<void>('POST', `/queries/${topicId}/reject`, token, { indices });
  },

  // Phase D — 整张申请
  async listTopicReviews(token: string, status?: SubmissionStatus): Promise<TopicReviewListItem[]> {
    const qs = status ? `?status=${status}` : '';
    return request<TopicReviewListItem[]>('GET', `/topics${qs}`, token);
  },
  async getTopicReview(topicId: number, token: string): Promise<TopicReviewDetail> {
    return request<TopicReviewDetail>('GET', `/topic/${topicId}`, token);
  },
  async patchTopic(topicId: number, payload: AdminPatchTopicPayload, token: string): Promise<TopicReviewDetail> {
    return request<TopicReviewDetail>('PATCH', `/topic/${topicId}`, token, payload);
  },
  async approveTopic(topicId: number, token: string): Promise<ExecutionPlan> {
    return request<ExecutionPlan>('POST', `/topic/${topicId}/approve`, token);
  },
  async rejectTopic(topicId: number, reason: string, token: string): Promise<void> {
    return request<void>('POST', `/topic/${topicId}/reject`, token, { reason });
  },
  async rerunTopic(topicId: number, token: string): Promise<ExecutionPlan> {
    return request<ExecutionPlan>('POST', `/topic/${topicId}/rerun`, token);
  },
  async getExecutionPlan(topicId: number, token: string): Promise<ExecutionPlan> {
    return request<ExecutionPlan>('GET', `/topic/${topicId}/execution-plan`, token);
  },

  // ── v3.3 战略方案 ─────────────────────────
  async getStrategicSolution(topicId: number, token: string): Promise<TopicStrategicSolution> {
    // 后端无记录时返回 status='idle' 的存根,所以这里不需要 null 分支
    return request<TopicStrategicSolution>('GET', `/topic/${topicId}/solution`, token);
  },
  async generateStrategicSolution(
    topicId: number, websiteUrl: string, token: string,
  ): Promise<TopicStrategicSolution> {
    return request<TopicStrategicSolution>(
      'POST', `/topic/${topicId}/solution/generate`, token,
      { website_url: websiteUrl },
    );
  },
};
