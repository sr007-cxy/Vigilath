// Shared type definitions for the sentiment monitoring module.
// Field names strictly match the backend (yuqin SQLite + GEO sentiment_accounts).

export type RunStatus = 'pending' | 'running' | 'success' | 'failed' | null;
export type SentimentLabel = 'bullish' | 'bearish' | 'neutral' | 'mixed' | 'unknown';
export type RiskLevel = 'none' | 'low' | 'medium' | 'high';
export type DraftVariant = 'conservative' | 'standard' | 'proactive';

// ─────────────── Keyword categories (matching the scale of clients' actual configs) ───────────────
// Clients maintain 6+ categories and hundreds of keywords in Excel; we carry them with structured groups.

export type KeywordKind =
  | 'entity'      // Company entity (target + subsidiaries + aliases + project codenames)
  | 'legal'       // Courts / litigation / lawsuits
  | 'exec'        // Executives (name + title prefix, expanded into "entity × name" combinations)
  | 'project'     // Project codenames (e.g. Reits / Acme Interconnect / AIDC)
  | 'competitor'  // Peer companies (for comparative analysis, not the brand entity)
  | 'custom';     // User-defined

export interface KeywordItem {
  term: string;            // Keyword text (a single word or a space-separated "entity modifier")
  is_new?: boolean;        // Yellow highlight = newly added
  enabled?: boolean;       // Default true
  note?: string;           // Note
}

export interface KeywordGroup {
  name: string;            // Display name: 「公司主体」 ("Company entity") / 「法院诉讼」 ("Court litigation") / custom
  kind: KeywordKind;       // Category
  items: KeywordItem[];
}

export interface SentimentAccount {
  id: number;
  user_id: number;
  target: string;
  /** Stock ticker -- optional; empty string for non-listed entities */
  ticker: string;
  /** Storage symbol used for fetching (= ticker or brand name). Always use it to fetch posts/briefs/today. */
  symbol: string;
  aliases: string[];
  intent: string | null;
  /** Old field: flat keyword list (backward compatible). New code uses keyword_groups. */
  keywords: string[];
  excludes: string[];
  /** Structured keyword groups — 6+ categories, multiple entries each. */
  keyword_groups?: KeywordGroup[];
  /** Media allowlist (list of platform codes, empty array = all platforms). */
  media_allowlist: string[];
  notify_emails: string[];
  /** List of WeChat official account collection URLs — weekly watch on KOL/competitor official accounts. Empty = disabled. */
  weixin_album_urls: string[];
  /** List of newsnow source ids (e.g. ['weibo','zhihu','v2ex']). Empty = disabled. */
  newsnow_sources: string[];
  /** What hour to run each day (Beijing time 0-23). null = unspecified, following the system cadence. Only shifts the time, doesn't change billing. */
  run_hour: number | null;
  active: boolean;
  last_run_at: string | null;
  last_run_status: RunStatus;
  last_run_error: string | null;
  created_at: string;
}

export interface AccountCreatePayload {
  enterprise_user_id?: number;
  target: string;
  /** Optional; pass an empty string for non-listed entities */
  ticker?: string;
  aliases?: string[];
  intent?: string;
  keywords?: string[];
  keyword_groups?: KeywordGroup[];
  excludes?: string[];
  media_allowlist?: string[];
  notify_emails?: string[];
  weixin_album_urls?: string[];
  newsnow_sources?: string[];
  run_hour?: number | null;
  run_now?: boolean;
}

export type AccountUpdatePayload = Partial<{
  target: string;
  ticker: string;
  aliases: string[];
  intent: string;
  keywords: string[];
  keyword_groups: KeywordGroup[];
  excludes: string[];
  media_allowlist: string[];
  notify_emails: string[];
  weixin_album_urls: string[];
  newsnow_sources: string[];
  run_hour: number;
  active: boolean;
}>;

/** Storage symbol used for fetching -- the backend's `symbol` (= stock ticker, or the brand name if not filled in).
 *  Old responses may not have this field yet, so fall back to ticker / target, lest the whole page's query
 *  be disabled by `enabled: !!ticker` because of an empty string. */
export function accountSymbol(acc: Pick<SentimentAccount, 'symbol' | 'ticker' | 'target'>): string {
  return acc.symbol || acc.ticker || acc.target;
}

/** Expand structured groups into a flat keywords list (sent to yuqin).
 *  kind is not distinguished; all enabled entries are concatenated in.
 */
export function flattenKeywordGroups(groups: KeywordGroup[]): string[] {
  const out: string[] = [];
  for (const g of groups) {
    for (const it of g.items) {
      if (it.enabled === false) continue;
      const t = it.term.trim();
      if (t) out.push(t);
    }
  }
  return Array.from(new Set(out));    // Deduplicate
}

export interface RunStatusPayload {
  status: RunStatus;
  last_run_at: string | null;
  error: string | null;
}

export interface SentimentWebSearchSource {
  title: string;
  url: string;
  domain?: string;
  snippet?: string;
  position?: number;
  query?: string;
}

export interface SentimentWebSearchRisk {
  title: string;
  detail?: string;
  severity?: 'low' | 'medium' | 'high' | string;
  source_indexes?: number[];
}

export interface SentimentWebSearchLatestResponse {
  available: boolean;
  id?: number;
  report_id?: number;
  mode?: 'yuanbao_websearch' | string | null;
  provider?: 'yuanbao' | string;
  target?: string;
  ticker?: string;
  generated_at?: string;
  query_count?: number;
  valid_results?: number;
  risk_level?: RiskLevel | string;
  sentiment_label?: SentimentLabel | string;
  sentiment_score?: number;
  summary?: string;
  llm_model?: string | null;
  positive_points?: string[];
  negative_points?: string[];
  risks?: SentimentWebSearchRisk[];
  suggestions?: string[];
  sources?: SentimentWebSearchSource[];
  queries?: string[];
  status?: RunStatus;
  error?: string | null;
  last_run_at?: string | null;
}

export interface RiskSignal {
  type: string;
  reason: string;
}

/** posts JOIN analyses rows (the shape returned by yuqin sentinel-service).
 *  Note: JSON fields are deserialized by _row_to_dict, so the frontend gets arrays rather than JSON strings.
 */
export interface SentimentPost {
  source: string;
  post_id: string;
  symbol: string;
  title: string | null;
  author: string | null;
  publish_time: string | null;
  ingested_at?: string | null;
  url: string | null;
  content: string | null;
  view_count: number | null;
  reply_count: number | null;
  like_count?: number | null;
  share_count?: number | null;
  // analyses
  is_relevant: 0 | 1 | boolean;
  filter_reason: string | null;
  summary: string | null;
  sentiment_label: SentimentLabel | string | null;
  sentiment_score: number | null;
  emotions?: string[];
  topics?: string[];
  entities?: string[];
  stance: string | null;
  intent: string | null;
  factuality: string | null;
  risk_level: RiskLevel | string | null;
  risk_signals?: RiskSignal[];
  influence_potential: number | null;
  hidden_meaning: string | null;
  citations?: string[];
  reasoning: string | null;
  model: string | null;
  analyzed_at: string | null;
}

export interface PostsResponse {
  count: number;
  items: SentimentPost[];
  total?: number;
  offset?: number;
  limit?: number;
  days?: number;
  start?: string | null;
  end?: string | null;
}

// Sort keys — one-to-one with backend _SORT_SQL.keys().
// Default newest (matching "latest articles (default)" in the reference image).
export type PostSortKey =
  | 'newest' | 'oldest'
  | 'engagement' | 'replies' | 'likes' | 'shares' | 'views'
  | 'cluster'
  | 'mediaAZ' | 'mediaZA'
  | 'scoreSentiment' | 'scoreInfluence';

export interface PostsQuery {
  limit?: number;
  offset?: number;
  /** days and start/end are mutually exclusive; the backend prefers start/end */
  days?: number;
  start?: string;
  end?: string;
  sort_by?: PostSortKey;
}

export interface BriefSummary {
  id: number;
  symbol: string;
  date: string;
  model: string;
  generated_at: string;
}

export interface Brief extends BriefSummary {
  body: string;
}

export interface BriefsResponse {
  count: number;
  items: BriefSummary[];
}

export interface DraftRow {
  id: number;
  symbol: string;
  source: string | null;
  post_id: string | null;
  topic: string | null;
  variant: DraftVariant | string;
  body: string;
  rationale: string | null;
  predicted_effect: string | null;
  cautions: string | null;
  model: string;
  generated_at: string;
}

export interface DraftsResponse {
  count: number;
  items: DraftRow[];
}

/** Shape returned directly by the draft generation endpoint (three tiers + metadata) */
export interface DraftGenerateResult {
  summary: string;
  drafts: {
    variant: DraftVariant;
    body: string;
    rationale: string | null;
    predicted_effect: string | null;
    cautions: string | null;
  }[];
  recommendation: DraftVariant;
  hitl_required: boolean;
  hitl_notes: string;
}

export interface KnowledgeDoc {
  key: 'brand_voice' | 'legal_redlines' | 'response_playbook' | string;
  body: string;
  updated_at: string;
}

export type MediaType =
  | 'web' | 'weibo' | 'wechat' | 'app' | 'forum'
  | 'short_video' | 'long_video' | 'broadcast'
  | 'newspaper' | 'magazine' | 'blog' | 'ecommerce' | 'review';

export type Industry =
  | 'finance' | 'general' | 'tech' | 'science'
  | 'party_media' | 'health' | 'overseas_en';

/** Media platform catalog entry (global, read-only, returned by GET /api/sentiment/platforms).
 *  - media_type: carrier format (web media/Weibo/WeChat/APP/forum/short video/long video/...)
 *  - industry: topic/industry (finance and economics/general news/technology/popular science/party and central government media/...)
 *  - region: region
 *  category is the legacy single axis, no longer consumed by the frontend (the backend SentimentPlatformOut still outputs it
 *  for external compatibility), kept as an optional string. */
export interface SentimentPlatform {
  code: string;
  domain: string;
  category?: string;                            // legacy
  media_type: MediaType | string;
  industry: Industry | string;
  region: 'mainland' | 'hk' | 'overseas' | string;
  name_zh: string;
  name_en: string;
}

export interface TodayKpi {
  total_today: number;
  high_risk: number;
  avg_sentiment: number;
  active_sources: number;
  trend_total_pct: number;
  trend_risk_pct: number;
  trend_sentiment_pct: number;
}

export interface SentimentTrendRow {
  date: string;
  bullish: number;
  neutral: number;
  bearish: number;
  mixed: number;
}

export interface RiskDistRow {
  level: RiskLevel;
  count: number;
  color: string;
}

export interface TodayResponse {
  ticker: string;
  kpi: TodayKpi;
  sentiment_trend: SentimentTrendRow[];
  risk_dist: RiskDistRow[];
  latest_brief: Brief | null;
  top_posts: SentimentPost[];
}

// ─────────────── newsnow real-time trending (not bound to an account) ──────────────
// Very thin data: only title + url + minimal extra (Weibo/Zhihu occasionally include hotness).
// No timestamp / no body / no author - that is the ceiling of the newsnow API.
// fetched_at is injected by the backend and means "the moment of this pull".

export type NewsnowCategory = 'social' | 'news' | 'tech' | 'finance' | string;

export interface NewsnowSource {
  id: string;
  name_zh: string;
  name_en: string;
  category: NewsnowCategory;
}

export interface NewsnowSourcesResponse {
  count: number;
  items: NewsnowSource[];
}

export interface NewsnowItem {
  id?: string | number;
  title: string;
  url?: string;
  mobileUrl?: string;
  /** newsnow occasionally stuffs hotness / icon / info into extra; the frontend renders it as a string */
  extra?: {
    info?: string;
    icon?: string;
    hover?: string;
    [k: string]: unknown;
  };
}

export interface NewsnowHotResponse {
  source: string;
  items: NewsnowItem[];
  count: number;
  fetched_at: string;
}
