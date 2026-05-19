// 品牌增长页 i18n 字典 — zh + en 双套,components 用 useBgLang() 拿
//
// 不走 i18next 全局键名(避免改动现有 zh.ts/en.ts 上千行结构),
// 用一个本地字典 + i18n.language 判定挑选,简单可控。

import i18n from '../../i18n';

interface Dict {
  // 通用
  loading: string;
  noTopic: string;
  topicPicker: string;
  back: string;
  viewAll: string;
  clearFilter: string;
  industryBaselineMissing: string;
  // 主页
  pageTitle: string;
  metricCitations: string;
  metricOwnedCitations: string;
  metricOtherCitations: string;
  blockRadar: string;
  blockEntries: string;
  blockCoreMetrics: string;
  blockBriefings: string;
  blockPublished: string;
  emptyBriefings: string;
  emptyPublished: string;
  // 6 入口卡
  entrySources: string;
  entryEngines: string;
  entryCompetitors: string;
  entryMatrix: string;
  entryInsights: string;
  entryQueries: string;
  entryEnginesSub: string;
  entryCompetitorsSub: string;
  entryMatrixSub: string;
  entryInsightsSub: string;
  entryQueriesSub: string;
  entrySourcesEmpty: string;
  // 核心指标
  metricTop1: string;
  metricTop3: string;
  metricTop5: string;
  metricVisible: string;
  metricSource: string;
  industryLabel: string;
  // 信源分析
  sourcesTitle: string;
  sourcesOwnedVsThirdParty: string;
  sourcesTopComposition: string;
  sourcesTopList: (n: number) => string;
  sourcesOwnedList: string;
  sourcesThirdPartyList: string;
  sourcesFilterAll: string;
  sourcesFilterOwned: string;
  sourcesFilterThird: string;
  sourcesOwnedLegend: string;
  sourcesThirdLegend: string;
  sourcesCenterOwned: string;
  sourcesCenterTotal: string;
  sourcesViewSamples: string;
  sourcesDrawerTitle: (domain: string) => string;
  sourcesDrawerEmpty: (period: number) => string;
  sourcesNoData: string;
  // 平台分析
  enginesTitle: string;
  enginesCitationsChart: string;
  enginesOverview: string;
  enginesHeatmap: string;
  enginesCitationsLabel: string;
  enginesHeatmapEngineCol: string;
  // 竞品分析
  competitorsTitle: string;
  competitorsSaiv: string;
  competitorsSaivCenter: string;
  competitorsBrand: (name: string) => string;
  competitorsRivalsTotal: string;
  competitorsTopList: string;
  competitorsPositionDist: string;
  competitorsSubstitutions: (filter: string | null) => string;
  competitorsSubsEmpty: string;
  competitorsSubColQuery: string;
  competitorsSubColRival: string;
  competitorsSubColCount: string;
  competitorsSubColEvidence: string;
  competitorsSubColAction: string;
  competitorsSubViewMatrix: string;
  // AI 词测验 / 矩阵
  matrixTitle: string;
  matrixHitRate: string;
  matrixDrawerTitleSep: string;
  matrixDrawerClose: string;
  matrixCellHit: string;
  matrixCellMiss: string;
  layerAll: string;
  layerTop1: string;
  layerTop3: string;
  layerTop5: string;
  layerVisible: string;
  layerSource: string;
  // 智能洞察
  insightsTitle: string;
  insightsBriefingList: string;
  insightsGenerate: string;
  insightsGenerating: string;
  insightsBriefingItem: (s: string, e: string) => string;
  insightsBriefingGenerated: (t: string) => string;
  insightsPick: string;
  insightsBriefingMeta: (s: string, e: string, m: string) => string;
  insightsRecommendations: string;
  insightsRecReason: string;
  // 关键词
  queriesTitle: string;
  queriesSummary: (q: number, e: number) => string;
  queriesColQuery: string;
  queriesColHitRate: string;
  queriesColRuns: string;
  queriesColHits: string;
  queriesColFirstEngine: string;
  queriesNeverHit: string;
  queriesIntentBlock: string;
  queriesIntentHint: string;
  queriesViewMatrix: string;
  // 原始引用
  responsesTitle: string;
  responsesFilterSummary: (e: string | undefined, q: string | undefined, p: number, n: number) => string;
  responsesExpand: string;
  responsesCollapse: string;
  responsesCitationsTitle: string;
  responsesEmpty: string;
  responsesHit: string;
  responsesMiss: string;
  // 投放战果
  publishedTitle: string;
  publishedFilterAll: string;
  publishedFilterHit: string;
  publishedFilterMiss: string;
  publishedAllPlatform: string;
  publishedTotal: (n: number) => string;
  publishedEmpty: string;
  publishedSourceAi: string;
  publishedSourceUser: string;
  publishedAt: string;
  publishedAiCited: string;
  publishedRelQuery: string;
  publishedRelQueryArrow: string;
  // 位置
  posLead: string;
  posBody: string;
  posTail: string;
  posUnknown: string;
  // 模块说明(? 悬浮文案)
  hintTopMetrics: string;
  hintRadar: string;
  hintEntries: string;
  hintCoreMetrics: string;
  hintBriefings: string;
  hintPublished: string;
  hintSources: string;
  hintEngines: string;
  hintCompetitors: string;
  hintMatrix: string;
  hintInsights: string;
  hintQueries: string;
  hintIntentBreakdown: string;
}

const ZH: Dict = {
  loading: '加载中…',
  noTopic: '无主题',
  topicPicker: '主题',
  back: '返回',
  viewAll: '查看全部 →',
  clearFilter: '清除筛选',
  industryBaselineMissing: '行业基准样本不足',
  pageTitle: '品牌增长',
  metricCitations: '推荐总词数',
  metricOwnedCitations: '权威媒体推荐数',
  metricOtherCitations: '第三方引用总数',
  blockRadar: '趋势分图(5 维占比)',
  blockEntries: '功能入口',
  blockCoreMetrics: '核心指标表现',
  blockBriefings: '报告明细',
  blockPublished: '投放战果',
  emptyBriefings: '暂无周报,可在「智能洞察」一键生成',
  emptyPublished: '暂无已投放内容',
  entrySources: '信源分析',
  entryEngines: '平台分析',
  entryCompetitors: '竞品分析',
  entryMatrix: '问题命中矩阵',
  entryInsights: '智能洞察',
  entryQueries: '监测问题',
  entryEnginesSub: '覆盖引擎',
  entryCompetitorsSub: '看竞品 + 替代证据 →',
  entryMatrixSub: '查询 × 引擎 命中详情 →',
  entryInsightsSub: '周报 + 优化建议',
  entryQueriesSub: '只读 · 在 admin 工作台编辑',
  entrySourcesEmpty: '暂无信源数据',
  metricTop1: 'Top1 占比',
  metricTop3: 'Top3 占比',
  metricTop5: 'Top5 占比',
  metricVisible: '可见占比',
  metricSource: '被引用占比',
  industryLabel: '行业 P50',
  sourcesTitle: '信源分析',
  sourcesOwnedVsThirdParty: '自有 vs 第三方',
  sourcesTopComposition: 'Top 域名构成',
  sourcesTopList: (n: number) => `Top ${n} 引用域名`,
  sourcesOwnedList: '自有 / 权威域名',
  sourcesThirdPartyList: '第三方域名',
  sourcesFilterAll: '全部',
  sourcesFilterOwned: '自有 / 权威',
  sourcesFilterThird: '第三方',
  sourcesOwnedLegend: '自有 / 权威',
  sourcesThirdLegend: '第三方',
  sourcesCenterOwned: '自有占比',
  sourcesCenterTotal: '总引用',
  sourcesViewSamples: '看样本 →',
  sourcesDrawerTitle: (d: string) => `引用样本 · ${d}`,
  sourcesDrawerEmpty: (p: number) => `该域名近 ${p} 天无引用样本`,
  sourcesNoData: '该过滤条件下暂无数据',
  enginesTitle: '平台分析',
  enginesCitationsChart: '各引擎引用次数',
  enginesOverview: '引擎概览',
  enginesHeatmap: '引擎 × 域名 热力图',
  enginesCitationsLabel: '引用',
  enginesHeatmapEngineCol: '引擎',
  competitorsTitle: '竞品分析',
  competitorsSaiv: '声量份额 SAIV',
  competitorsSaivCenter: '本品占比',
  competitorsBrand: (n: string) => `本品(${n})`,
  competitorsRivalsTotal: '竞品总和',
  competitorsTopList: '竞品排行 Top 10',
  competitorsPositionDist: '命中位置分布',
  competitorsSubstitutions: (f: string | null) =>
    `被替代证据 — 提了竞品没提我${f ? `(${f})` : ''}`,
  competitorsSubsEmpty: '暂无替代证据(或本期未抽取出竞品)',
  competitorsSubColQuery: '查询',
  competitorsSubColRival: '竞品',
  competitorsSubColCount: '次数',
  competitorsSubColEvidence: '证据',
  competitorsSubColAction: '',
  competitorsSubViewMatrix: '查矩阵 →',
  matrixTitle: '问题命中矩阵',
  matrixHitRate: '命中率',
  matrixDrawerTitleSep: ' × ',
  matrixDrawerClose: '关闭',
  matrixCellHit: '命中',
  matrixCellMiss: '未命中',
  layerAll: '全部',
  layerTop1: 'Top1',
  layerTop3: 'Top3',
  layerTop5: 'Top5',
  layerVisible: '可见',
  layerSource: '被引用',
  insightsTitle: '智能洞察',
  insightsBriefingList: '周报列表',
  insightsGenerate: '新生成',
  insightsGenerating: '生成中…',
  insightsBriefingItem: (s: string, e: string) => `${s} → ${e}`,
  insightsBriefingGenerated: (t: string) => `生成于 ${t}`,
  insightsPick: '选择左侧周报查看,或点「新生成」',
  insightsBriefingMeta: (s: string, e: string, m: string) => `${s} → ${e} · 模型 ${m}`,
  insightsRecommendations: '建议行动',
  insightsRecReason: '理由',
  queriesTitle: '监测问题',
  queriesSummary: (q: number, e: number) => `共 ${q} 条问题 · ${e} 个引擎 · 编辑入口在 admin 工作台`,
  queriesColQuery: '问题',
  queriesColHitRate: '命中率',
  queriesColRuns: '跑批数',
  queriesColHits: '命中数',
  queriesColFirstEngine: '首次命中引擎',
  queriesNeverHit: '尚未命中',
  queriesIntentBlock: '问题主题分布',
  queriesIntentHint: '同类问题归到一组,看品牌在哪类问题上曝光最弱',
  queriesViewMatrix: '查矩阵 →',
  responsesTitle: '原始引用',
  responsesFilterSummary: (e, q, p, n) => {
    const parts: string[] = [];
    if (e) parts.push(`引擎: ${e}`);
    if (q) parts.push(`问题: ${q}`);
    parts.push(`近 ${p} 天共 ${n} 条`);
    return parts.join(' · ');
  },
  responsesExpand: '展开正文',
  responsesCollapse: '收起',
  responsesCitationsTitle: '引用清单',
  responsesEmpty: '(无正文)',
  responsesHit: '命中',
  responsesMiss: '未命中',
  publishedTitle: '投放战果',
  publishedFilterAll: '全部',
  publishedFilterHit: '已被 AI 引用',
  publishedFilterMiss: '未被引用',
  publishedAllPlatform: '全部平台',
  publishedTotal: (n: number) => `共 ${n} 篇已投放`,
  publishedEmpty: '该筛选下暂无已投放内容',
  publishedSourceAi: 'AI 生成',
  publishedSourceUser: '用户提交',
  publishedAt: '已发布',
  publishedAiCited: 'AI 引用',
  publishedRelQuery: '关联问题',
  publishedRelQueryArrow: ' → 看 AI 现在怎么答',
  posLead: '开头',
  posBody: '中段',
  posTail: '末尾',
  posUnknown: '未知',
  hintTopMetrics: '本期 AI 引擎在所有问题里提到品牌的累计次数,以及自有 / 权威媒体 vs 第三方来源拆分',
  hintRadar: '5 个维度叠在一张图上看品牌健康度:Top1/Top3/Top5 是排名第几位,可见 = 任意提到,被引用 = 不同问题里至少被引一次',
  hintEntries: '点任意卡片进对应子页查看明细',
  hintCoreMetrics: '基于 LLM 抽取的 brand_rank(品牌在答复里第几个被提到)算占比;行业 P50 来自同行业多个租户的中位数',
  hintBriefings: 'LLM 自动总结的周期报告,包含本期表现 + 优化建议',
  hintPublished: '已发布的稿件,点 chip 跳外站。若有"AI 引用"绿标说明该投放被 AI 答复正文引用过',
  hintSources: 'AI 答复里被作为信源的域名分布;自有 / 权威 = 域名命中品牌关键词,其他算第三方',
  hintEngines: '不同 AI 引擎对品牌的引用频次 + 引用了哪些第三方域名(热力图越深说明该引擎依赖该域)',
  hintCompetitors: '把品牌和被 LLM 抽出的竞品对比;"被替代证据" 列出"问题里提了竞品但没提你"的具体证据',
  hintMatrix: '每个 (问题 × 引擎) cell 的命中状态。深绿 = Top1,中绿 = Top3,浅绿 = Top5,灰 = 未命中。点格看历次答复',
  hintInsights: '每周自动生成一份 LLM 周报:本期 KPI + 新命中 / 流失的 cell + 下周行动建议',
  hintQueries: '所有监测问题的命中状况,只读 — 编辑入口在 admin 工作台',
  hintIntentBreakdown: '把语义相近的问题聚成一组(如"价格类"、"对比类"),看哪类问题品牌曝光最弱,内容补强的方向就清楚了',
};

const EN: Dict = {
  loading: 'Loading…',
  noTopic: 'No topic',
  topicPicker: 'Topic',
  back: 'Back',
  viewAll: 'View all →',
  clearFilter: 'Clear filter',
  industryBaselineMissing: 'Industry baseline: not enough samples',
  pageTitle: 'Brand Growth',
  metricCitations: 'Total Citations',
  metricOwnedCitations: 'Authoritative Citations',
  metricOtherCitations: 'Third-party Citations',
  blockRadar: 'Trend (5-dim breakdown)',
  blockEntries: 'Modules',
  blockCoreMetrics: 'Core Metrics',
  blockBriefings: 'Briefings',
  blockPublished: 'Published Content',
  emptyBriefings: 'No briefings yet — generate one from Insights',
  emptyPublished: 'No published content',
  entrySources: 'Sources',
  entryEngines: 'Engines',
  entryCompetitors: 'Competitors',
  entryMatrix: 'Query Matrix',
  entryInsights: 'Insights',
  entryQueries: 'Tracked Queries',
  entryEnginesSub: 'engines covered',
  entryCompetitorsSub: 'rivals + substitution evidence →',
  entryMatrixSub: 'query × engine hit detail →',
  entryInsightsSub: 'briefings + recommendations',
  entryQueriesSub: 'read-only · edit in admin',
  entrySourcesEmpty: 'no source data yet',
  metricTop1: 'Top-1 share',
  metricTop3: 'Top-3 share',
  metricTop5: 'Top-5 share',
  metricVisible: 'Visibility',
  metricSource: 'Sourced rate',
  industryLabel: 'Industry P50',
  sourcesTitle: 'Sources',
  sourcesOwnedVsThirdParty: 'Owned vs Third-party',
  sourcesTopComposition: 'Top domains composition',
  sourcesTopList: (n) => `Top ${n} cited domains`,
  sourcesOwnedList: 'Owned / authoritative domains',
  sourcesThirdPartyList: 'Third-party domains',
  sourcesFilterAll: 'All',
  sourcesFilterOwned: 'Owned / authoritative',
  sourcesFilterThird: 'Third-party',
  sourcesOwnedLegend: 'Owned',
  sourcesThirdLegend: 'Third-party',
  sourcesCenterOwned: 'Owned share',
  sourcesCenterTotal: 'Total citations',
  sourcesViewSamples: 'View samples →',
  sourcesDrawerTitle: (d) => `Citation samples · ${d}`,
  sourcesDrawerEmpty: (p) => `No samples in last ${p} days`,
  sourcesNoData: 'No data for this filter',
  enginesTitle: 'Engines',
  enginesCitationsChart: 'Citations per engine',
  enginesOverview: 'Engine overview',
  enginesHeatmap: 'Engine × Domain heatmap',
  enginesCitationsLabel: 'citations',
  enginesHeatmapEngineCol: 'Engine',
  competitorsTitle: 'Competitors',
  competitorsSaiv: 'Share of AI Voice (SAIV)',
  competitorsSaivCenter: 'Brand share',
  competitorsBrand: (n) => `Brand (${n})`,
  competitorsRivalsTotal: 'Rivals total',
  competitorsTopList: 'Top 10 rivals',
  competitorsPositionDist: 'Mention-position distribution',
  competitorsSubstitutions: (f) =>
    `Substitution evidence — rival mentioned, brand not${f ? ` (${f})` : ''}`,
  competitorsSubsEmpty: 'No substitution evidence yet',
  competitorsSubColQuery: 'Query',
  competitorsSubColRival: 'Rival',
  competitorsSubColCount: 'Count',
  competitorsSubColEvidence: 'Evidence',
  competitorsSubColAction: '',
  competitorsSubViewMatrix: 'See matrix →',
  matrixTitle: 'Query Matrix',
  matrixHitRate: 'Hit rate',
  matrixDrawerTitleSep: ' × ',
  matrixDrawerClose: 'Close',
  matrixCellHit: 'hit',
  matrixCellMiss: 'miss',
  layerAll: 'All',
  layerTop1: 'Top-1',
  layerTop3: 'Top-3',
  layerTop5: 'Top-5',
  layerVisible: 'Visible',
  layerSource: 'Sourced',
  insightsTitle: 'Insights',
  insightsBriefingList: 'Briefings',
  insightsGenerate: 'Generate',
  insightsGenerating: 'Generating…',
  insightsBriefingItem: (s, e) => `${s} → ${e}`,
  insightsBriefingGenerated: (t) => `Generated ${t}`,
  insightsPick: 'Pick a briefing on the left, or click Generate',
  insightsBriefingMeta: (s, e, m) => `${s} → ${e} · model ${m}`,
  insightsRecommendations: 'Recommendations',
  insightsRecReason: 'Reason',
  queriesTitle: 'Tracked Queries',
  queriesSummary: (q, e) => `${q} queries · ${e} engines · edit in admin`,
  queriesColQuery: 'Query',
  queriesColHitRate: 'Hit rate',
  queriesColRuns: 'Runs',
  queriesColHits: 'Hits',
  queriesColFirstEngine: 'First-hit engine',
  queriesNeverHit: 'never hit',
  queriesIntentBlock: 'Query topic distribution',
  queriesIntentHint: 'Similar queries grouped; weakest groups are where you most need content',
  queriesViewMatrix: 'See matrix →',
  responsesTitle: 'Raw responses',
  responsesFilterSummary: (e, q, p, n) => {
    const parts: string[] = [];
    if (e) parts.push(`engine: ${e}`);
    if (q) parts.push(`query: ${q}`);
    parts.push(`last ${p}d, ${n} rows`);
    return parts.join(' · ');
  },
  responsesExpand: 'Expand',
  responsesCollapse: 'Collapse',
  responsesCitationsTitle: 'Citations',
  responsesEmpty: '(empty)',
  responsesHit: 'hit',
  responsesMiss: 'miss',
  publishedTitle: 'Published Content',
  publishedFilterAll: 'All',
  publishedFilterHit: 'Cited by AI',
  publishedFilterMiss: 'Not cited',
  publishedAllPlatform: 'All platforms',
  publishedTotal: (n) => `${n} published`,
  publishedEmpty: 'No published content for this filter',
  publishedSourceAi: 'AI generated',
  publishedSourceUser: 'User submitted',
  publishedAt: 'Published',
  publishedAiCited: 'Cited by',
  publishedRelQuery: 'Related query',
  publishedRelQueryArrow: ' → see how AI answers now',
  posLead: 'lead',
  posBody: 'body',
  posTail: 'tail',
  posUnknown: 'unknown',
  hintTopMetrics: 'Cumulative AI-engine mentions for the brand this period, split by owned/authoritative vs third-party sources',
  hintRadar: '5 dimensions in one view: Top1/Top3/Top5 = position rank, Visible = any mention, Sourced = unique queries with at least one hit',
  hintEntries: 'Click any card to drill into the sub-page',
  hintCoreMetrics: 'Based on LLM-extracted brand_rank (the brand’s position in the answer). Industry P50 = median across same-industry tenants',
  hintBriefings: 'Periodic LLM summary: this-period KPIs + recommendations',
  hintPublished: 'Published docs. Chips link to external posts; the green "cited by" badge means the AI engine quoted this URL in its answer',
  hintSources: 'Domains cited by AI engines. Owned = domain matches brand keywords; everything else counts as third-party',
  hintEngines: 'Per-engine citation frequency and which third-party domains it cites (darker heatmap = the engine relies more on that domain)',
  hintCompetitors: 'Brand vs LLM-extracted competitors. "Substitution evidence" lists queries where the rival is mentioned but the brand isn’t',
  hintMatrix: 'Hit state per (query × engine). Deep green = Top1, mid green = Top3, light green = Top5, gray = miss. Click a cell to see history',
  hintInsights: 'Weekly LLM briefing: KPIs + newly-hit / lost cells + next-week actions',
  hintQueries: 'All tracked queries (read-only). Edit in admin workbench',
  hintIntentBreakdown: 'Semantically-similar queries grouped together (e.g. "pricing", "comparison"). See which group your brand is weakest in — that’s where you need more content',
};

export function useBgLang(): Dict {
  const lng = (i18n.language || 'zh').toLowerCase();
  return lng.startsWith('en') ? EN : ZH;
}

// ── 静态映射 — 不依赖 hook 的场景(图表 label / chart palette) ──

export const ENGINE_LABELS_ZH: Record<string, string> = {
  deepseek: 'DeepSeek',
  doubao: '豆包',
  qwen: '通义千问',
  wenxin: '文心一言',
  yuanbao: '元宝',
  chatgpt: 'ChatGPT',
  claude: 'Claude',
  gemini: 'Gemini',
  grok: 'Grok',
  copilot: 'Copilot',
};

export const ENGINE_LABELS_EN: Record<string, string> = {
  deepseek: 'DeepSeek',
  doubao: 'Doubao',
  qwen: 'Qwen',
  wenxin: 'Wenxin',
  yuanbao: 'Yuanbao',
  chatgpt: 'ChatGPT',
  claude: 'Claude',
  gemini: 'Gemini',
  grok: 'Grok',
  copilot: 'Copilot',
};

export function engineLabel(id: string): string {
  const lng = (i18n.language || 'zh').toLowerCase();
  const map = lng.startsWith('en') ? ENGINE_LABELS_EN : ENGINE_LABELS_ZH;
  return map[id] || id;
}
