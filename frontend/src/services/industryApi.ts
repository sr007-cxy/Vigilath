// Industry library API — connects to backend geo/api/industry_library.py (public anonymous endpoints, no token)
// The competitive payload is isomorphic with the report center's CompetitiveAnalysis, so the type is reused directly.

import { localizedHeaders, readApiError } from './apiError';
import type { CompetitiveAnalysis } from './reportsApi';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

async function request<T>(path: string): Promise<T> {
  const resp = await fetch(`${API_BASE}/industry${path}`, {
    method: 'GET',
    headers: localizedHeaders(),
  });
  if (!resp.ok) {
    const msg = await readApiError(resp, `Request GET ${path} failed`);
    throw new Error(msg);
  }
  return resp.json() as Promise<T>;
}

export interface IndustrySummary {
  key: string;
  name: string;
  brand_count: number;
  keyword_count: number;
  engines: string[];
  updated: string;
  top_brands: string[];
}

export interface IndustryVisMetric {
  current: number;
  delta: number | null;
  series: number[];
  series_keys: string[];
}

export interface IndustryBrandCard {
  name: string;
  rank: number;
  coverage: number;
  coverage_vs_avg: number;
  mention: number;
  mention_vs_avg: number;
}

export interface IndustrySourceRow {
  domain: string;
  count: number;
  engines: string[];
  sample_title: string;
}

export interface IndustryHeatWord {
  query: string;
  heat: number;
  source: 'monitor' | 'suggest';                 // Prompts (real audit heat) / Baidu autocomplete related terms
}

export interface IndustrySources {
  total_citations: number;
  type_share: { source_type: string; count: number; pct: number }[];
  top_sources: IndustrySourceRow[];
}

export interface IndustryData {
  key: string;
  name: string;
  keyword_count: number;
  brand_count: number;
  updated: string;
  heat: IndustryVisMetric;                       // Industry share of voice (total brand mentions)
  top1: { name: string; coverage: IndustryVisMetric };
  cr3: IndustryVisMetric;                        // Brand concentration (share of voice of the top three brands)
  industry_avg_coverage: number;
  industry_avg_mention: number;
  brands: IndustryBrandCard[];                   // Leading brand comparison (Top3, vs. industry average)
  top1_gap: number;
  opportunity_keywords: number;
  sources: IndustrySources;                      // Citation source analysis
  heat_words: IndustryHeatWord[];                // Word cloud terms (prompts + related search terms)
  competitive: CompetitiveAnalysis;              // Leaderboard / engine list
}

export const industryApi = {
  list: () => request<{ industries: IndustrySummary[] }>('/list'),
  data: (key: string) => request<IndustryData>(`/${encodeURIComponent(key)}/data`),
};
