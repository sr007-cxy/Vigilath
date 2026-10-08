// Compliance audit center API client (P0) — ledger / compliance profile / audit timeline.
// Design doc: chapter 8 of docs/06-发布运维/合规与资质/合规中心/合规审计中心-整体设计.md.

import { localizedHeaders, readApiError } from './apiError';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

export type HitLevel = 'ban' | 'warn' | 'evid';
export type ScanVerdict = 'pass' | 'returned' | 'evidence_required';

export interface ComplianceLexiconInfo {
  id: number;
  code: string;
  name: string;
  tier: string;
  version: string;
  term_count: number;
  on: boolean;
}

export interface LexiconTerm {
  term: string;
  level: HitLevel;
  category: string;
  fix: string;
  aliases: string[];
}

export interface LexiconDetail {
  lexicon: {
    id: number; code: string; name: string; tier: string;
    version: string; rules_text: string; term_count: number;
  };
  items: LexiconTerm[];
}

export interface ExternalScanInfo {
  service?: string;
  status?: 'ok' | 'partial' | 'degraded' | 'skipped';
  risk_level?: string;
  labels?: string[];
  risk_words?: { word: string; tips: string; level: string }[];
  elapsed_ms?: number;
  truncated?: boolean;
  reason?: string;
}

export interface QualificationStatus {
  type: string;
  ref: string;
  valid_until: string;
  status: 'ok' | 'expiring' | 'expired' | 'missing';
}

export interface ComplianceProfile {
  topic_id: number;
  industry: string;
  industry_tier: string;
  ymyl_tier: number;               // 1/2/3; 0 = non-YMYL
  admission?: { allowed: boolean; reason: string };   // Gate 0: category placeability verdict
  required_qualification_types?: string[];            // Gate 0: required qualification type config
  review_layers: number;
  sample_pct: number;
  log_retention_days: number;
  archive_retention_years: number;
  restrictions: string[];
  lexicons: ComplianceLexiconInfo[];
  qualifications: QualificationStatus[];
}

export interface ComplianceKpis {
  days: number;
  scanned_docs: number;
  returned_docs: number;
  alerts: number | null;
  alerts_closed: number | null;
  avg_disposal_hours: number | null;
  log_retained_days: number;
}

export interface ScanListItem {
  scan_id: number;
  doc_id: number;
  scanned_at: string | null;
  hook: string;
  title: string;
  channel: string;
  doc_status: string;
  verdict: ScanVerdict;
  ban_count: number;
  warn_count: number;
  evid_count: number;
  missing_disclaimers: string[];
  hits_brief: { term: string; level: HitLevel }[];
  exempted_count: number;        // Number of hits judged non-violating by semantic review, excluded from the verdict
  chain_label: string;
  chain_tone: string;
}

// Semantic review exemption: lexicon hits that don't constitute a claim in context (第一 in 「第一步」 ("first step"), 唯一 in 「唯一标识」 ("unique identifier"))
export interface HitExemption {
  offset: [number, number];
  by: 'rule' | 'llm';
  reason: string;
  quote: string;
}

export interface ScanHit {
  term: string;
  level: HitLevel;
  category: string;
  fix: string;
  sources: string[];
  offsets: [number, number][];   // Positions still counted as violations (exempted positions aren't here and aren't highlighted)
  exemptions?: HitExemption[];
  suppressed?: boolean;          // All hit positions exempted -> the whole item is excluded from the verdict
}

export interface ScanDetail {
  scan_id: number;
  doc_id: number;
  scanned_at: string | null;
  hook: string;
  verdict: ScanVerdict;
  lexicon_versions: Record<string, string>;
  hits: ScanHit[];
  exempted: ScanHit[];           // Hits fully exempted after semantic review, shown only and excluded from the verdict
  missing_disclaimers: { label: string; hint: string }[];
  external?: ExternalScanInfo;
  doc: {
    id: number;
    title: string;
    body_markdown: string;
    status: string;
    reject_reason: string;
    platform: string;
  };
  trace: {
    at: string | null;
    event_type: string;
    actor_type: string;
    actor_name: string;
    detail: Record<string, unknown>;
  }[];
}

// ── In-flight monitoring (P1) ──────────────────────────────────
export type AlertType =
  | 'fabricated_credential' | 'fake_case' | 'disparage_competitor'
  | 'appearance_anxiety' | 'unauthorized_channel' | 'category_redline'
  | 'banned_wording';

export type AlertStatus = 'open' | 'confirmed' | 'dismissed' | 'closed';
export type TicketAction = 'limit_fix' | 'takedown' | 'suspend';
export type TicketStatus = 'open' | 'in_progress' | 'pending_retest' | 'closed' | 'overdue';

export interface AlertTicketBrief {
  id: number;
  ticket_no: string;
  action: TicketAction;
  status: TicketStatus;
  deadline_at: string | null;
  clean_streak: number;
  retest_count: number;
  resolution: string;
}

export interface AlertItem {
  id: number;
  alert_type: AlertType;
  level: 1 | 2 | 3;
  detected_by: 'lexicon' | 'llm' | 'manual';
  confidence: number | null;
  status: AlertStatus;
  quote: string;
  reason: string;
  channel: string;
  engine: string;
  query: string;
  found_at: string | null;
  source_url: string;
  answer_excerpt: string;
  recurrence_count: number;
  handled_by_name: string;
  ticket: AlertTicketBrief | null;
}

export interface TimelineItem {
  id: number;
  log_id: string;
  at: string | null;
  event_type: string;
  object_type: string;
  object_id: number | null;
  actor_type: string;
  actor_name: string;
  detail: Record<string, unknown>;
}

interface Paged<T> {
  total: number;
  page: number;
  page_size: number;
  items: T[];
}

async function request<T>(method: string, path: string, token: string, body?: unknown): Promise<T> {
  const init: RequestInit = {
    method,
    headers: localizedHeaders({
      Authorization: `Bearer ${token}`,
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    }),
  };
  if (body !== undefined) init.body = JSON.stringify(body);
  const resp = await fetch(`${API_BASE}/compliance${path}`, init);
  if (!resp.ok) {
    const msg = await readApiError(resp, `Request ${method} ${path} failed`);
    throw new Error(msg);
  }
  return resp.json() as Promise<T>;
}

export const complianceApi = {
  getProfile: (topicId: number, token: string) =>
    request<ComplianceProfile>('GET', `/topics/${topicId}/profile`, token),

  getKpis: (topicId: number, token: string, days = 30) =>
    request<ComplianceKpis>('GET', `/topics/${topicId}/kpis?days=${days}`, token),

  listScans: (topicId: number, token: string, page = 1, pageSize = 20) =>
    request<Paged<ScanListItem>>(
      'GET', `/topics/${topicId}/scans?page=${page}&page_size=${pageSize}`, token),

  getScanDetail: (scanId: number, token: string) =>
    request<ScanDetail>('GET', `/scans/${scanId}`, token),

  getLexiconTerms: (lexiconId: number, token: string) =>
    request<LexiconDetail>('GET', `/lexicons/${lexiconId}/terms`, token),

  getTimeline: (topicId: number, token: string, page = 1, pageSize = 30) =>
    request<Paged<TimelineItem>>(
      'GET', `/topics/${topicId}/timeline?page=${page}&page_size=${pageSize}`, token),

  listAlerts: (topicId: number, token: string, page = 1, pageSize = 50) =>
    request<Paged<AlertItem>>(
      'GET', `/topics/${topicId}/alerts?page=${page}&page_size=${pageSize}`, token),

  confirmAlert: (alertId: number, token: string, action: TicketAction, note = '') =>
    request<AlertItem>('POST', `/alerts/${alertId}/confirm`, token, { action, note }),

  dismissAlert: (alertId: number, token: string, reason = '') =>
    request<AlertItem>('POST', `/alerts/${alertId}/dismiss`, token, { reason }),

  ticketAction: (ticketId: number, token: string, mark: 'start' | 'done' | 'extend_deadline',
                 note = '', newDeadlineAt?: string) =>
    request<{ ok: boolean; id: number; status: string; deadline_at: string | null }>(
      'POST', `/tickets/${ticketId}/action`, token,
      { mark, note, new_deadline_at: newDeadlineAt }),

  putQualifications: (
    topicId: number, token: string,
    qualifications: { type: string; ref?: string; valid_until?: string; evidence_ref?: string }[],
    requiredTypes?: string[],
  ) =>
    request<{ ok: boolean; qualifications: QualificationStatus[] }>(
      'PUT', `/topics/${topicId}/qualifications`, token,
      { qualifications, required_qualification_types: requiredTypes }),
};
