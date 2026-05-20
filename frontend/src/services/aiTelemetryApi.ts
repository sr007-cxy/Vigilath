// AI 遥测 API 客户端 — 主题 CRUD + 立即试跑.
// 后端就绪前用 mock 数据;VITE_USE_MOCK_AI_TELEMETRY=1 强制 mock.

import { localizedHeaders, readApiError } from './apiError';

const API_BASE = (import.meta.env.VITE_API_URL as string) || '/api';

export type EngineId =
  | 'deepseek' | 'doubao' | 'qwen' | 'wenxin' | 'yuanbao'
  | 'chatgpt' | 'claude' | 'gemini' | 'grok' | 'copilot';

export const CN_ENGINES: EngineId[] = ['deepseek', 'doubao', 'qwen', 'wenxin', 'yuanbao'];
export const GLOBAL_ENGINES: EngineId[] = ['chatgpt', 'claude', 'gemini', 'grok', 'copilot'];

export type ReviewStatus = 'pending' | 'approved' | 'rejected';

export interface SeedPrompt {
  text: string;
  status: ReviewStatus;
  submitted_at?: string | null;
  approved_at?: string | null;
  rejected_at?: string | null;
  reviewer_id?: number | null;
}

export interface Topic {
  id: number;
  name: string;
  target: string;
  target_aliases: string[];
  industry: string;
  queries: string[];
  query_cluster_ids?: number[];      // 与 queries 同长,可能为空(老主题或未聚类)
  query_statuses?: ReviewStatus[];   // Phase C — 与 queries 同长,legacy 默认 approved
  query_selected?: boolean[];        // Phase D — 与 queries 同长,标记是否选为监测问题
  query_seeds?: string[];            // 2026-05-20 — 与 queries 同长,种子提示词(legacy 为 "")
  clusters?: ClusterMetaPersist[];   // picker 端聚类后的簇元数据
  seed_prompts?: SeedPrompt[];       // Phase C — 已提交的种子词列表
  engines: EngineId[];
  enabled: boolean;
  last_run_at?: string | null;
  last_run_status?: 'success' | 'failed' | 'running' | null;
  // Phase D — 资料 + 申请状态机
  profile?: BrandProfile;
  submission_status?: SubmissionStatus;
  submitted_at?: string | null;
  approved_at?: string | null;
  rejected_at?: string | null;
  reviewer_id?: number | null;
  selected_query_count?: number;
  // 修订号:每次编辑都 +1。后端 _append_changelog 是单一来源。
  version?: number;
  // Phase D.1 — AI 自动生成排程
  auto_generate_enabled?: boolean;
  auto_generate_time?: string;    // "HH:MM"
  auto_generate_count?: number;
  auto_generate_last_run_at?: string | null;
  // v1.4 — admin 配的扩展提示词;TopicOut 返回但普通用户不渲染
  prompt_extension?: string | null;
  created_at?: string;
  updated_at?: string;
}

export type SubmissionStatus = 'draft' | 'pending' | 'approved' | 'rejected';

// Phase D — 品牌资料 (6 大模块)
export interface BrandProfile {
  // 一、资料基础标识
  profile_name: string;
  company_full_name: string;
  company_short_name: string;
  industry: string;
  core_business_lines: string[];
  service_geo: string;
  // 二、内容创作方向
  creation_directions: string[];
  copywriting_types: string[];
  target_platforms: string[];
  content_tones: string[];
  content_redlines: string[];
  // 三、品牌主体信息
  team_size: string;
  founded_year: string;
  core_credentials: string[];
  brand_diff_tags: string[];
  // 四、产品 / 服务核心信息
  core_service_overview: string;
  service_features: string[];
  service_process: string[];
  target_scenarios: string[];
  service_guarantees: string[];
  // 四、品牌故事与情感素材(原五,2026-05-17 删了「目标用户与痛点」节)
  brand_story: string;
  key_person_story: string;
  case_stories: string[];
  brand_values: string;
  // 五、补充素材与创作边界(原七)
  available_materials: string[];
  brand_slogan: string;
  core_message: string;
  extra_notes: string;
}

export const EMPTY_BRAND_PROFILE: BrandProfile = {
  profile_name: '', company_full_name: '', company_short_name: '',
  industry: '', core_business_lines: [], service_geo: '',
  creation_directions: [], copywriting_types: [], target_platforms: [],
  content_tones: [], content_redlines: [],
  team_size: '', founded_year: '', core_credentials: [], brand_diff_tags: [],
  core_service_overview: '', service_features: [], service_process: [],
  target_scenarios: [], service_guarantees: [],
  brand_story: '', key_person_story: '', case_stories: [], brand_values: '',
  available_materials: [], brand_slogan: '', core_message: '', extra_notes: '',
};

export const MAX_SELECTED_QUERIES = 50;
// 单次 seed 扩展候选上限(用户从这一批里再勾 ≤ MAX_SELECTED_QUERIES 个)
export const MAX_EXPANSION_CANDIDATES = 200;

export interface ClusterMetaPersist {
  cluster_id: number;
  label: string;
  size: number;
}

export interface TopicPayload {
  name: string;
  target: string;
  target_aliases: string[];
  industry: string;
  queries: string[];
  query_cluster_ids?: number[];
  clusters?: ClusterMetaPersist[];
  // 2026-05-20 — 与 queries 同长,每条 query 来自哪个种子提示词。
  // picker 按种子分组,保存时把映射一并传后端,持久化到 queries_json[].seed。
  query_seeds?: string[];
  engines: EngineId[];
  enabled: boolean;
  // Phase C — 创建 / 更新时把当前种子词附带提交,后端追加进 seed_prompts_json (pending);
  // 编辑器允许多条种子串行扇出,因此接 list。字段名跟 Topic.seed_prompts (对象数组)区分.
  seed_drafts?: string[];
  // Phase D — 同请求带资料;后端写 profile_json + 追加 changelog。
  profile?: BrandProfile;
  // v1.4 — admin 工作台编辑时可写;普通用户编辑时即使带也会被后端忽略
  prompt_extension?: string | null;
}

export interface RunNowResult {
  engine: EngineId;
  query: string;
  answer: string;
  citations: { url: string; domain: string; title: string }[];
  error?: string | null;
}

export interface QueryCandidate {
  text: string;
  score: number;
  sources: string[];
  cluster_id?: number;
  // 2026-05-20 — 这条候选是哪个种子提示词扩展出来的。
  // picker 用它做"按种子分组渲染"(不再用 cluster_id)。
  seed?: string;
}

export interface ClusterMeta {
  cluster_id: number;
  label: string;
  size: number;
  medoid_index: number;
}

export interface RunSummary {
  id: number;
  topic_id: number;
  status: 'running' | 'success' | 'failed';
  started_at: string;
  finished_at: string | null;
  error: string | null;
  response_count: number;
}

export interface ResponseRow {
  id: number;
  engine: EngineId;
  query: string;
  answer: string;
  citations: { url: string; domain: string; title: string }[];
  video_url: string | null;
  source_url?: string | null;
  error: string | null;
  created_at: string;
  hit?: boolean;
  hit_excerpt?: string | null;
  mention_position?: string | null;
  brand_rank?: number | null;
}

// ─── v1 引用追踪 ──────────────────────────────────────────

export type CellStatus = 'pending' | 'running' | 'done';

export interface QueryHitCell {
  query: string;
  engine: EngineId;
  status: CellStatus;
  first_hit_at: string | null;
  first_hit_response_id: number | null;
  last_checked_at: string | null;
  total_runs: number;
  total_hits: number;
}

export interface EngineFirstHit {
  engine: EngineId;
  first_hit_at: string | null;
  first_hit_query: string | null;
  days_after_start: number | null;
}

export interface TrackingMatrix {
  topic_id: number;
  target: string;
  target_aliases: string[];
  queries: string[];
  engines: EngineId[];
  started_at: string;
  cells: QueryHitCell[];
  timeline: EngineFirstHit[];
  total_runs: number;
  total_cells: number;
  hit_cells: number;
  hit_cells_pct: number;
}

export interface CellEvidence {
  response_id: number;
  run_id: number;
  created_at: string;
  engine: EngineId;
  query: string;
  hit: boolean;
  hit_excerpt: string | null;
  mention_position?: 'lead' | 'body' | 'tail' | 'unknown' | string | null;
  source_url: string | null;
  answer: string;
  citations: { url: string; domain: string; title: string }[];
}

// ─── v1.3 SAIV / 竞品份额 ──────────────────────────────────

export interface CompetitorShareEntry {
  name: string;
  count: number;
  pct: number;
}

export interface PositionDist {
  lead: number;
  body: number;
  tail: number;
  unknown: number;
}

export interface ShareOfVoice {
  topic_id: number;
  target: string;
  period_days: number;
  brand_count: number;
  competitors_count_total: number;
  saiv_pct: number;
  competitors: CompetitorShareEntry[];
  position_dist: PositionDist;
  optimal_rate_pct: number;
  total_runs: number;
  sample_size: number;
}

export interface CellInsightRec {
  priority: 'P0' | 'P1' | 'P2' | string;
  title: string;
  action: string;
  why: string;
}

export interface CompetitorMention {
  name: string;
  count: number;
  snippet: string;
}

export interface CellInsight {
  id: number;
  topic_id: number;
  query: string;
  engine: EngineId;
  window_start: string;
  window_end: string;
  verdict: 'hit_stable' | 'hit_unstable' | 'near_miss' | 'no_signal' | 'negative_mention' | string;
  summary: string;
  competitors_top3: CompetitorMention[];
  recommendations: CellInsightRec[];
  answer_format: string | null;
  citation_domains: string[];
  evidence_response_ids: number[];
  llm_model: string;
  prompt_version: string;
  generated_at: string;
  feedback: 'helpful' | 'not_helpful' | 'wrong' | null;
}

export interface CellDrawer {
  cell: QueryHitCell;
  evidence: CellEvidence[];
  insight: CellInsight | null;
}

// ─── v1.2 周报 ────────────────────────────────────────────

export interface BriefingAction {
  priority: string;
  title: string;
  why: string;
  how: string;
}

export interface Briefing {
  id: number;
  topic_id: number;
  period_start: string;
  period_end: string;
  body_md: string;
  kpi_snapshot: Record<string, unknown>;
  top_actions: BriefingAction[];
  delivered_email_at: string | null;
  feedback_score: number | null;
  llm_model: string;
  prompt_version: string;
  generated_at: string;
}

export interface KpiBlock {
  value: number;
  delta_pct: number | null;
  sparkline: number[];
}

export interface TrendPoint {
  date: string;
  values: Partial<Record<EngineId, number>>;
}

export interface DomainCount {
  domain: string;
  count: number;
  pct: number;
}

export interface OwnedSplit {
  owned: number;
  other: number;
  owned_pct: number;
  delta_pct: number | null;
}

export interface Overview {
  topic_id: number;
  period_days: number;
  brand_keywords: string[];
  visibility: KpiBlock;
  citations: KpiBlock;
  growth: KpiBlock;
  engines_covered: KpiBlock;
  engines_total: number;
  trend: TrendPoint[];
  engines: EngineId[];
  top_domains: DomainCount[];
  owned_split: OwnedSplit;
  engine_domain_matrix: Partial<Record<EngineId, Record<string, number>>>;
}

export interface ClusterBreakdownItem {
  cluster_id: number;
  label: string;
  query_count: number;
  response_count: number;
  mention_count: number;
  mention_rate: number;
  citation_count: number;
}

export interface IntentBreakdown {
  topic_id: number;
  period_days: number;
  brand_keywords: string[];
  clusters: ClusterBreakdownItem[];
  uncategorized: ClusterBreakdownItem;
}

// ── 品牌增长页 — 雷达 5 维 + 行业基准 + 竞品替代证据 ───────
export interface PositionBreakdown {
  top1_pct: number;
  top3_pct: number;
  top5_pct: number;
  visible_pct: number;
  source_pct: number;
}

export interface PositionBreakdownResp {
  topic_id: number;
  period_days: number;
  industry: string;
  total_cells: number;
  total_queries: number;
  breakdown: PositionBreakdown;
  industry_baseline: PositionBreakdown | null;
}

export interface IndustryBenchmark {
  industry: string;
  sample_size: number;
  breakdown: PositionBreakdown | null;
}

export interface CompetitorSubstitutionItem {
  query: string;
  competitor_name: string;
  competitor_count: number;
  sample_response_id: number;
  sample_snippet: string;
}

export interface AdminAccount {
  id: number;
  email: string;
  name?: string | null;
  is_active: boolean;
  is_admin: boolean;
  topic_count: number;
  has_prompt_extension: boolean;
}

export interface CompetitorSubstitutionResp {
  topic_id: number;
  period_days: number;
  competitor_filter: string | null;
  items: CompetitorSubstitutionItem[];
  total: number;
}


export function isMockMode(): boolean {
  return String(import.meta.env.VITE_USE_MOCK_AI_TELEMETRY || '').toLowerCase() === '1';
}

// ── mock state ──────────────────────────────────────────────
let _mockSeq = 100;
const _mockTopics: Topic[] = [
  {
    id: 1,
    name: 'VNET 品牌问询',
    target: '世纪互联',
    target_aliases: ['VNET', 'Century Link China'],
    industry: 'IDC 数据中心',
    queries: ['世纪互联怎么样', '国内 IDC 服务商推荐', 'VNET vs 万国数据'],
    engines: ['deepseek', 'doubao', 'qwen', 'wenxin', 'yuanbao', 'chatgpt'],
    enabled: true,
    last_run_at: new Date(Date.now() - 2 * 3600 * 1000).toISOString(),
    last_run_status: 'success',
  },
];

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

export const aiTelemetryApi = {
  async listTopics(token: string): Promise<Topic[]> {
    if (isMockMode()) return Promise.resolve([..._mockTopics]);
    return request<Topic[]>('GET', '/topics', token);
  },

  async createTopic(payload: TopicPayload, token: string): Promise<Topic> {
    if (isMockMode()) {
      const t: Topic = {
        id: _mockSeq++, ...payload,
        last_run_at: null, last_run_status: null,
      };
      _mockTopics.push(t);
      return Promise.resolve(t);
    }
    return request<Topic>('POST', '/topics', token, payload);
  },

  async updateTopic(id: number, payload: TopicPayload, token: string): Promise<Topic> {
    if (isMockMode()) {
      const i = _mockTopics.findIndex(t => t.id === id);
      if (i >= 0) _mockTopics[i] = { ..._mockTopics[i], ...payload };
      return Promise.resolve(_mockTopics[i]);
    }
    return request<Topic>('PUT', `/topics/${id}`, token, payload);
  },

  async deleteTopic(id: number, token: string): Promise<void> {
    if (isMockMode()) {
      const i = _mockTopics.findIndex(t => t.id === id);
      if (i >= 0) _mockTopics.splice(i, 1);
      return Promise.resolve();
    }
    return request<void>('DELETE', `/topics/${id}`, token);
  },

  // Phase C — 甲方追加种子提示词(待审核)
  async submitSeedPrompt(topicId: number, text: string, token: string): Promise<Topic> {
    return request<Topic>('POST', `/topics/${topicId}/seed-prompts`, token, { text });
  },

  async suggestQueries(
    args: {
      seed: string; count: number;
      target?: string; aliases?: string[]; industry?: string;
      // 2026-05-20 — 用户在 profile 里填的服务地域。
      // 非空时 prompt 会把所有 query 的地点维度锁定在该地域(+ 全国 / 跨地区),
      // 不再随机扩到其它城市/国家。
      service_geo?: string;
    },
    token: string,
  ): Promise<{ seed: string; queries: QueryCandidate[]; clusters: ClusterMeta[] }> {
    if (isMockMode()) {
      // mock: 拼出 args.count 条假候选 + 5 簇 + 递减分数,够前端 picker 调试
      const stems = ['是什么', '怎么样', '推荐', '对比', '替代方案', '价格', '评价', '案例', '使用场景', '行业应用'];
      const out: QueryCandidate[] = [];
      const clusterLabels = ['认知与定义', '对比与差异', '价格与费用', '案例与口碑', '使用与场景'];
      for (let i = 0; out.length < args.count; i++) {
        out.push({
          text: `${args.seed} ${stems[i % stems.length]} ${Math.floor(i / stems.length) + 1}`,
          score: Math.max(20, 90 - i),
          sources: i % 4 === 0 ? ['suggest:baidu'] : ['llm:deepseek'],
          cluster_id: i % 5,
        });
      }
      const clusters: ClusterMeta[] = clusterLabels.map((l, i) => ({
        cluster_id: i,
        label: l,
        size: out.filter(q => q.cluster_id === i).length,
        medoid_index: out.findIndex(q => q.cluster_id === i),
      }));
      return Promise.resolve({ seed: args.seed, queries: out, clusters });
    }
    return request<{ seed: string; queries: QueryCandidate[]; clusters: ClusterMeta[] }>(
      'POST', '/suggest-queries', token, {
        seed: args.seed, count: args.count,
        target: args.target || '',
        aliases: args.aliases || [],
        industry: args.industry || '',
        service_geo: args.service_geo || '',
      },
    );
  },

  async runNow(payload: TopicPayload, token: string): Promise<RunNowResult[]> {
    if (isMockMode()) {
      return Promise.resolve(
        payload.engines.flatMap(engine =>
          payload.queries.map(query => ({
            engine,
            query,
            answer: `[mock] ${engine} 关于 "${query}" 的回答...`,
            citations: [{ url: 'https://example.com', domain: 'example.com', title: 'Example' }],
            error: null,
          }))
        )
      );
    }
    return request<RunNowResult[]>('POST', '/topics/run-now', token, payload);
  },

  async triggerRun(topicId: number, token: string): Promise<{ status: string; topic_id: number }> {
    if (isMockMode()) return Promise.resolve({ status: 'started', topic_id: topicId });
    return request('POST', `/topics/${topicId}/run`, token);
  },

  async listRuns(topicId: number, token: string, limit = 20): Promise<RunSummary[]> {
    if (isMockMode()) return Promise.resolve([]);
    return request<RunSummary[]>('GET', `/topics/${topicId}/runs?limit=${limit}`, token);
  },

  async listResponses(runId: number, token: string): Promise<ResponseRow[]> {
    if (isMockMode()) return Promise.resolve([]);
    return request<ResponseRow[]>('GET', `/runs/${runId}/responses`, token);
  },

  async getOverview(topicId: number, periodDays: number, token: string): Promise<Overview> {
    if (isMockMode()) {
      const trend = Array.from({ length: periodDays }, (_, i) => ({
        date: new Date(Date.now() - (periodDays - 1 - i) * 86400000).toISOString().slice(0, 10),
        values: { deepseek: Math.floor(Math.random() * 10), qwen: Math.floor(Math.random() * 8) },
      }));
      return Promise.resolve({
        topic_id: topicId, period_days: periodDays,
        brand_keywords: ['mock'],
        visibility: { value: 78, delta_pct: 12, sparkline: trend.map(t => t.values.deepseek || 0) },
        citations: { value: 1248, delta_pct: 16, sparkline: trend.map(t => t.values.deepseek || 0) },
        growth: { value: 32, delta_pct: null, sparkline: [] },
        engines_covered: { value: 5, delta_pct: null, sparkline: [] },
        engines_total: 10,
        trend,
        engines: ['deepseek', 'qwen'],
        top_domains: [
          { domain: 'zhihu.com', count: 42, pct: 18 },
          { domain: 'baike.baidu.com', count: 28, pct: 12 },
        ],
        owned_split: { owned: 24, other: 200, owned_pct: 10.7, delta_pct: 2.1 },
        engine_domain_matrix: { deepseek: { 'zhihu.com': 8 }, qwen: { 'baike.baidu.com': 5 } },
      });
    }
    return request<Overview>('GET', `/topics/${topicId}/overview?period=${periodDays}`, token);
  },

  // ── v1.3 SAIV / 竞品份额 / 优选率 / 命中位置 ────────
  async getShareOfVoice(
    topicId: number, periodDays: number, token: string,
  ): Promise<ShareOfVoice> {
    if (isMockMode()) return Promise.resolve(_mockSoV(topicId, periodDays));
    return request<ShareOfVoice>('GET', `/topics/${topicId}/share-of-voice?period=${periodDays}`, token);
  },

  // 按 picker 端聚出的 intent cluster 把本期 response 分组聚合
  async getIntentBreakdown(
    topicId: number, periodDays: number, token: string,
  ): Promise<IntentBreakdown> {
    if (isMockMode()) {
      const mk = (cid: number, label: string, qc: number, rc: number, mc: number, cc: number) => ({
        cluster_id: cid, label, query_count: qc, response_count: rc,
        mention_count: mc,
        mention_rate: rc ? Math.round(mc / rc * 1000) / 1000 : 0,
        citation_count: cc,
      });
      return Promise.resolve({
        topic_id: topicId, period_days: periodDays, brand_keywords: ['mock'],
        clusters: [
          mk(0, '认知与定义', 12, 84, 31, 96),
          mk(1, '对比与差异', 10, 70, 18, 64),
          mk(2, '价格与费用', 8, 56, 38, 72),
          mk(3, '案例与口碑', 6, 42, 25, 50),
          mk(4, '使用与场景', 4, 28, 9, 22),
        ],
        uncategorized: mk(-1, 'uncategorized', 0, 0, 0, 0),
      });
    }
    return request<IntentBreakdown>(
      'GET', `/topics/${topicId}/intent-breakdown?period=${periodDays}`, token,
    );
  },

  // ── v1 引用追踪 ──────────────────────────
  async getTrackingMatrix(topicId: number, token: string): Promise<TrackingMatrix> {
    if (isMockMode()) return Promise.resolve(_mockMatrix(topicId));
    return request<TrackingMatrix>('GET', `/topics/${topicId}/tracking-matrix`, token);
  },

  async getCellDrawer(
    topicId: number, query: string, engine: EngineId, token: string,
  ): Promise<CellDrawer> {
    if (isMockMode()) return Promise.resolve(_mockDrawer(topicId, query, engine));
    const qs = new URLSearchParams({ query, engine }).toString();
    return request<CellDrawer>('GET', `/topics/${topicId}/cells/drawer?${qs}`, token);
  },

  // ── v1.1 LLM 诊断 ─────────────────────────
  async fetchCellInsight(
    topicId: number, query: string, engine: EngineId, token: string, force = false,
  ): Promise<CellInsight> {
    if (isMockMode()) return Promise.resolve(_mockInsight(topicId, query, engine));
    const qs = new URLSearchParams({ query, engine, force: String(force) }).toString();
    return request<CellInsight>('POST', `/topics/${topicId}/cells/insight?${qs}`, token);
  },

  async postCellInsightFeedback(
    insightId: number, feedback: 'helpful' | 'not_helpful' | 'wrong', token: string,
  ): Promise<void> {
    if (isMockMode()) return Promise.resolve();
    return request<void>('POST', `/cell-insights/${insightId}/feedback`, token, { feedback });
  },

  // ── v1.2 周报 ─────────────────────────────
  async listBriefings(topicId: number, token: string, limit = 12): Promise<Briefing[]> {
    if (isMockMode()) return Promise.resolve([_mockBriefing(topicId)]);
    return request<Briefing[]>('GET', `/topics/${topicId}/briefings?limit=${limit}`, token);
  },

  async getBriefing(briefingId: number, token: string): Promise<Briefing> {
    if (isMockMode()) return Promise.resolve(_mockBriefing(0, briefingId));
    return request<Briefing>('GET', `/briefings/${briefingId}`, token);
  },

  async triggerBriefing(topicId: number, token: string): Promise<Briefing> {
    if (isMockMode()) return Promise.resolve(_mockBriefing(topicId));
    return request<Briefing>('POST', `/topics/${topicId}/briefings/generate`, token);
  },

  async postBriefingFeedback(briefingId: number, score: number, token: string): Promise<void> {
    if (isMockMode()) return Promise.resolve();
    return request<void>('POST', `/briefings/${briefingId}/feedback`, token, { score });
  },

  // ── 品牌增长页 — Phase 1 后端聚合的真值接口 ─────────────
  async getPositionBreakdown(
    topicId: number, periodDays: number, token: string,
  ): Promise<PositionBreakdownResp> {
    return request<PositionBreakdownResp>(
      'GET', `/topics/${topicId}/position-breakdown?period=${periodDays}`, token,
    );
  },

  async getIndustryBenchmark(
    industry: string, periodDays: number, token: string,
  ): Promise<IndustryBenchmark> {
    const qs = new URLSearchParams({ industry, period: String(periodDays) }).toString();
    return request<IndustryBenchmark>('GET', `/benchmarks/industry?${qs}`, token);
  },

  async getCompetitorSubstitutions(
    topicId: number, periodDays: number, token: string,
    opts: { competitor?: string; limit?: number } = {},
  ): Promise<CompetitorSubstitutionResp> {
    const params = new URLSearchParams({ period: String(periodDays) });
    if (opts.competitor) params.set('competitor', opts.competitor);
    if (opts.limit) params.set('limit', String(opts.limit));
    return request<CompetitorSubstitutionResp>(
      'GET', `/topics/${topicId}/competitor-substitutions?${params.toString()}`, token,
    );
  },

  // ── admin 跨用户管理 ─────────────────────────────────
  async adminListAccounts(token: string): Promise<AdminAccount[]> {
    return request<AdminAccount[]>('GET', '/admin/accounts', token);
  },
  async adminListUserTopics(userId: number, token: string): Promise<Topic[]> {
    return request<Topic[]>('GET', `/admin/users/${userId}/topics`, token);
  },

  // admin 替指定 user 直接创建主题(跳过审核,落库即 approved)
  async adminCreateTopicForUser(
    userId: number, payload: TopicPayload, token: string,
  ): Promise<Topic> {
    return request<Topic>('POST', `/admin/users/${userId}/topics`, token, payload);
  },

  async listTopicResponses(
    topicId: number, token: string,
    opts: { engine?: string; domain?: string; query?: string; period?: number; limit?: number } = {},
  ): Promise<ResponseRow[]> {
    const params = new URLSearchParams();
    if (opts.engine) params.set('engine', opts.engine);
    if (opts.domain) params.set('domain', opts.domain);
    if (opts.query) params.set('query', opts.query);
    if (opts.period) params.set('period', String(opts.period));
    if (opts.limit) params.set('limit', String(opts.limit));
    const qs = params.toString();
    const suffix = qs ? `?${qs}` : '';
    return request<ResponseRow[]>('GET', `/topics/${topicId}/responses${suffix}`, token);
  },
};

// ── mock helpers ────────────────────────────────────────────
function _mockMatrix(topicId: number): TrackingMatrix {
  const queries = ['Q1 跨境TMT投资律师', 'Q2 私募股权律师', 'Q3 资本市场律师'];
  const engines: EngineId[] = ['wenxin', 'doubao', 'yuanbao'];
  const now = new Date();
  const cells: QueryHitCell[] = [];
  for (const q of queries) {
    for (const e of engines) {
      const hit = q === queries[0] && (e === 'wenxin' || e === 'doubao');
      cells.push({
        query: q, engine: e,
        status: hit ? 'done' : 'done',
        first_hit_at: hit ? new Date(now.getTime() - 86400000).toISOString() : null,
        first_hit_response_id: hit ? 1 : null,
        last_checked_at: now.toISOString(),
        total_runs: 3,
        total_hits: hit ? 2 : 0,
      });
    }
  }
  return {
    topic_id: topicId,
    target: '金诚同达律所', target_aliases: ['KWM', 'King & Wood'],
    queries, engines,
    started_at: new Date(now.getTime() - 5 * 86400000).toISOString(),
    cells,
    timeline: engines.map(e => ({
      engine: e,
      first_hit_at: e === 'wenxin' || e === 'doubao'
        ? new Date(now.getTime() - 86400000).toISOString() : null,
      first_hit_query: e === 'wenxin' || e === 'doubao' ? queries[0] : null,
      days_after_start: e === 'wenxin' ? 3 : (e === 'doubao' ? 3 : null),
    })),
    total_runs: 3,
    total_cells: queries.length * engines.length,
    hit_cells: 2,
    hit_cells_pct: Math.round(2 / (queries.length * engines.length) * 1000) / 10,
  };
}

function _mockDrawer(_topicId: number, query: string, engine: EngineId): CellDrawer {
  return {
    cell: {
      query, engine, status: 'done',
      first_hit_at: new Date(Date.now() - 86400000).toISOString(),
      first_hit_response_id: 1, last_checked_at: new Date().toISOString(),
      total_runs: 3, total_hits: 2,
    },
    evidence: [
      {
        response_id: 1, run_id: 1, created_at: new Date(Date.now() - 86400000).toISOString(),
        engine, query, hit: true,
        hit_excerpt: '…推荐关注 [金诚同达律所],在 TMT 行业有丰富积累…',
        source_url: 'https://yiyan.baidu.com/chat/abc',
        answer: 'mock answer body',
        citations: [{ url: 'https://gov.cn', domain: 'gov.cn', title: '示例' }],
      },
    ],
    insight: null,
  };
}

function _mockInsight(topicId: number, query: string, engine: EngineId): CellInsight {
  return {
    id: 1, topic_id: topicId, query, engine,
    window_start: new Date(Date.now() - 7 * 86400000).toISOString(),
    window_end: new Date().toISOString(),
    verdict: 'hit_unstable',
    summary: '近 7 天 3 次跑批命中 2 次,引擎倾向 listicle 答题套路',
    competitors_top3: [
      { name: '君合律师事务所', count: 3, snippet: '…在跨境投资领域推荐君合…' },
      { name: '中伦律师事务所', count: 2, snippet: '…中伦律师事务所的 TMT 团队…' },
    ],
    recommendations: [
      { priority: 'P0', title: '加 TMT 案例长文', action: '30 天内 jincheng.com 加 3 篇 TMT 海外并购深度案例', why: '该引擎对深度报告类内容权重高' },
      { priority: 'P1', title: '优化 PR 投放', action: '把"金诚同达 TMT 团队"投到 lawinfochina.com', why: '该域名在 citations 高频出现' },
    ],
    answer_format: 'listicle',
    citation_domains: ['gov.cn', 'lawinfochina.com'],
    evidence_response_ids: [1, 2, 3],
    llm_model: 'mock', prompt_version: 'cell_v1',
    generated_at: new Date().toISOString(),
    feedback: null,
  };
}

function _mockSoV(topicId: number, periodDays: number): ShareOfVoice {
  return {
    topic_id: topicId, target: '世纪互联', period_days: periodDays,
    brand_count: 23,
    competitors_count_total: 41,
    saiv_pct: 35.9,
    competitors: [
      { name: '万国数据', count: 18, pct: 28.1 },
      { name: '世纪华通', count: 12, pct: 18.8 },
      { name: '光环新网', count: 6, pct: 9.4 },
      { name: '宝信软件', count: 3, pct: 4.7 },
      { name: '数据港', count: 2, pct: 3.1 },
    ],
    position_dist: { lead: 12, body: 8, tail: 3, unknown: 0 },
    optimal_rate_pct: 82.9,
    total_runs: 7,
    sample_size: 35,
  };
}

function _mockBriefing(topicId: number, id = 1): Briefing {
  return {
    id, topic_id: topicId,
    period_start: new Date(Date.now() - 7 * 86400000).toISOString(),
    period_end: new Date().toISOString(),
    body_md: '## 本周亮点\n- 文心一言 Q1 完成首次命中\n\n## 下周优先行动\n- 加 TMT 案例长文',
    kpi_snapshot: { hit_rate: 22.2, ttfm_days: 3, new_hits_count: 2, lost_hits_count: 0 },
    top_actions: [
      { priority: 'P0', title: 'TMT 案例库扩容', why: '文心引擎偏好深度报告', how: '30 天内加 3 篇' },
    ],
    delivered_email_at: null, feedback_score: null,
    llm_model: 'mock', prompt_version: 'briefing_v1',
    generated_at: new Date().toISOString(),
  };
}
