// One-click full diagnosis API wrapper — generate -> poll GET every 3s -> render when ready -> export PDF.
// Backend: backend/geo/api/full_diagnosis.py (/api/check/full/*).

import axios from 'axios';
import { extractAxiosErrorMessage, currentLocale } from './apiError';

const API_BASE_URL = import.meta.env.VITE_API_URL || '/api';

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 60000, // No long-blocking requests under the polling protocol; PDF export is relaxed separately
  headers: { 'Content-Type': 'application/json' },
});

apiClient.interceptors.request.use((config) => {
  config.headers = config.headers || {};
  config.headers['X-Locale'] = currentLocale();
  const token = typeof window !== 'undefined' ? localStorage.getItem('token') : null;
  if (token) config.headers['Authorization'] = `Bearer ${token}`;
  return config;
});

export class FullDiagnosisApiError extends Error {
  status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.name = 'FullDiagnosisApiError';
    this.status = status;
  }
}

function throwApiError(error: unknown, fallback: string): never {
  if (axios.isAxiosError(error)) {
    throw new FullDiagnosisApiError(
      extractAxiosErrorMessage(error, fallback),
      error.response?.status,
    );
  }
  throw new FullDiagnosisApiError(fallback);
}

export type DiagnosisStepStatus = 'pending' | 'running' | 'ok' | 'failed' | 'skipped';

export interface DiagnosisStep {
  mode: string;
  status: DiagnosisStepStatus;
  error?: string | null;
}

export interface DiagnosisProgress {
  stage: 'checks' | 'llm';
  done: number;
  total: number;
  steps: DiagnosisStep[];
}

export interface DiagnosisDimension {
  key: string;
  score: number | null;
  weight: number;
  estimated: boolean;
  source: string;
}

export interface DiagnosisScores {
  total: number;
  grade: string;
  dimensions: DiagnosisDimension[];
}

export interface DiagnosisSubResult {
  status: 'ok' | 'failed' | 'skipped';
  data?: Record<string, unknown>;
  error?: string;
  reason?: string;
}

export interface DiagnosisNarrative {
  executive_summary?: string;
  key_findings?: { title: string; detail: string; severity: string }[];
  risks?: string[];
  dimension_notes?: Record<string, string>;
  personas?: { name: string; intent: string; scenario: string; typical_queries?: string[] }[];
  competitive_summary?: string;
  quick_wins?: { action: string; expected_impact: string; effort: string; expected_gain?: number }[];
  p1_actions?: { action: string; reason: string; eta_weeks?: number; expected_gain?: number }[];
  p2_actions?: { action: string; reason: string; eta_weeks?: number; expected_gain?: number }[];
  roadmap?: { phase: string; weeks: string; goals?: string[] }[];
  score_projection?: {
    current: number; after_30d: number; after_90d: number; assumptions?: string;
  } | null;
  sentiment_summary?: {
    risk_level: string; summary: string;
    positive_points?: string[];
    negative_points?: string[]; suggestions?: string[];
  } | null;
  dimension_projection?: Record<string, { current: number; target: number }>;
  competitor_profiles?: { name: string; strengths?: string[]; weaknesses?: string[] }[];
  brand_strengths?: string[];
  brand_weaknesses?: string[];
  // ── Narrative blocks added after the eleven-chapter reorganization (supplied by backend batch one; chapters use placeholders until ready) ──
  /** Chapter 04 Search scenes and intent */
  scene_intent?: {
    summary?: string;
    by_scene?: { scene: string; intent_desc?: string; finding?: string }[];
    gaps?: string[];
  } | null;
  /** Chapter 06 Interpretation of the six metrics */
  metrics_notes?: Partial<Record<MetricKey, string>> & {
    weakest?: MetricKey; summary?: string;
  } | null;
  /** Chapter 07 Website */
  infrastructure_note?: {
    summary?: string;
    top_issues?: { cluster?: string; issue: string; fix?: string; severity?: string }[];
    crawler_note?: string;
  } | null;
  /** Chapter 08 Authority endorsement */
  authority_note?: {
    summary?: string;
    strong_signals?: string[];
    missing_signals?: string[];
    actions?: { action: string; channel?: string; eta_weeks?: number }[];
  } | null;
  /** Chapter 11 Overall assessment and outlook */
  overall_assessment?: {
    verdict?: string;
    stage?: string;
    benchmark?: string;
    metric_projection?: {
      key: MetricKey; current: number; after_30d: number; after_90d: number; driver?: string;
    }[];
  } | null;
}

/** Keys of the six metrics, consistent with the pages/Reports side */
export type MetricKey = 'exposure' | 'top1' | 'top3' | 'accuracy' | 'citation' | 'mention';

export interface SentimentPostItem {
  title: string; source: string; url: string;
  /** Real event time (YYYY-MM-DD / YYYY-MM / YYYY); an empty string if unavailable, never backfilled with the batch-run date */
  publish_time: string;
  /** recent = within the recent window, historical = earlier, unknown = time unknown */
  recency?: 'recent' | 'historical' | 'unknown';
  sentiment_label: string; sentiment_score?: number | null;
  risk_level?: string; summary?: string;
}

export interface FullDiagnosisReport {
  id: number;
  status: 'generating' | 'ready' | 'failed';
  url: string;
  brand_name: string;
  competitor_domains: string[];
  industry_terms?: string[];
  brand_terms?: string[];
  progress?: DiagnosisProgress | null;
  scores?: DiagnosisScores | null;
  narrative?: DiagnosisNarrative | null;
  subresults?: Record<string, DiagnosisSubResult> | null;
  llm_model: string;
  error?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface FullDiagnosisSummary {
  id: number;
  url: string;
  brand_name: string;
  status: string;
  total_score?: number | null;
  grade?: string | null;
  created_at?: string | null;
}

export const fullDiagnosisApi = {
  async generate(
    url: string,
    brandName: string,
    competitorDomains: string[],
    industryTerms: string[] = [],
    brandTerms: string[] = [],
    // Audit scope. Empty = the backend default full (eight items, credits charged by query count × engine count);
    // 'lite' runs only the four site-side items and **burns not a single credit** -- used for on-site promotion: new accounts have a zero balance,
    // and running full directly would 402 on the spot, hitting users with an error right after they bind their phone number.
    scope?: 'full' | 'lite',
  ): Promise<{ report_id: number; status: string }> {
    try {
      const res = await apiClient.post('/check/full/generate', {
        url,
        brand_name: brandName,
        competitor_domains: competitorDomains,
        industry_terms: industryTerms,
        brand_terms: brandTerms,
        ...(scope ? { scope } : {}),
      });
      return res.data;
    } catch (error) {
      throwApiError(error, 'Failed to start diagnosis');
    }
  },

  // Extract industry term candidates from the brand/website for the frontend's "one-click extract, click to add". Returns an empty array on failure, never throws.
  async suggestIndustryTerms(brand: string, url: string): Promise<string[]> {
    try {
      const res = await apiClient.post('/check/suggest-industry-terms', { brand, url });
      return Array.isArray(res.data?.terms) ? res.data.terms as string[] : [];
    } catch (error) {
      throwApiError(error, '行业词提取失败');
    }
  },

  async get(reportId: number): Promise<FullDiagnosisReport> {
    try {
      const res = await apiClient.get(`/check/full/${reportId}`);
      return res.data;
    } catch (error) {
      throwApiError(error, 'Failed to load diagnosis report');
    }
  },

  async list(page = 1, size = 10): Promise<{ items: FullDiagnosisSummary[]; total: number; page: number; size: number }> {
    try {
      const res = await apiClient.get('/check/full', { params: { page, size } });
      return res.data;
    } catch (error) {
      throwApiError(error, 'Failed to load diagnosis history');
    }
  },

  async exportPdf(reportId: number, lang: string, theme: string, tz?: string): Promise<Blob> {
    try {
      const res = await apiClient.post(
        `/check/full/${reportId}/export-pdf`,
        null,
        { params: { lang, theme, tz }, responseType: 'blob', timeout: 120000 },
      );
      return res.data;
    } catch (error) {
      throwApiError(error, 'Failed to export PDF');
    }
  },
};
