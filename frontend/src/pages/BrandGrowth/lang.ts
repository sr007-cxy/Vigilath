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
  metricPublishedTotal: string;
  metricCitedTotal: string;
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
  // 模型筛选 chip(2026-05-21)
  engineSelectAll: string;
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
  insightsBriefingMeta: (s: string, e: string) => string;
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
  // 2026-05-20 — 新版 6 列表格(序号 / 种子提示词 / 扩展提示词 / 命中 / 模型 / 查看)
  queriesColIndex: string;
  queriesColSeed: string;
  queriesColExpansion: string;
  queriesColHit: string;
  queriesColModel: string;
  queriesColAction: string;
  queriesNoSeed: string;
  queriesFilterAllSeeds: string;
  queriesFilterAllModels: string;
  queriesEmptyAfterFilter: string;
  queriesViewMatrix: string;
  // 命中查看弹窗 (2026-05-20)
  queriesViewDetail: string;
  queriesDetailEmpty: string;
  queriesDetailHitBadge: string;
  queriesDetailMissBadge: string;
  queriesDetailClose: string;
  queriesDetailCitations: string;
  queriesDetailEnginesHeader: (n: number) => string;
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
  hintPublishedTotal: string;
  hintCitedTotal: string;
  hintRadar: string;
  hintEntries: string;
  hintCoreMetrics: string;
  hintMetricTop1: string;
  hintMetricTop5: string;
  hintMetricVisible: string;
  hintMetricSource: string;
  hintBriefings: string;
  hintPublished: string;
  hintPublishedCited: string;
  hintSources: string;
  hintSourcesStat: string;
  hintSourcesUnique: string;
  hintSourcesOwnedPct: string;
  hintSourcesFilter: string;
  hintSourcesDomainEngine: string;
  hintEngines: string;
  hintEnginesOverview: string;
  hintEnginesHeatmap: string;
  hintCompetitors: string;
  hintCompetitorsTop: string;
  hintCompetitorsPosition: string;
  hintCompetitorsSubs: string;
  hintMatrix: string;
  hintMatrixTimeline: string;
  hintInsights: string;
  hintInsightsView: string;
  hintQueries: string;
  hintQueriesTable: string;
  hintResponses: string;
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
  metricCitations: '推荐词总数',
  metricPublishedTotal: '媒体投放',
  metricCitedTotal: '引用总数',
  blockRadar: '趋势分图(5 维占比)',
  blockEntries: '功能入口',
  blockCoreMetrics: '核心指标表现',
  blockBriefings: '报告明细',
  blockPublished: '投放战果',
  emptyBriefings: '暂无监测问题,请到管理工作台添加',
  emptyPublished: '暂无已投放内容',
  entrySources: '信源分析',
  entryEngines: '平台分析',
  entryCompetitors: '竞品分析',
  entryMatrix: '问题命中矩阵',
  entryInsights: '智能洞察',
  entryQueries: '监测报告',
  entryEnginesSub: '覆盖引擎',
  entryCompetitorsSub: '看竞品 + 替代证据 →',
  entryMatrixSub: '查询 × 引擎 命中详情 →',
  entryInsightsSub: '周报 + 优化建议',
  entryQueriesSub: '监测问题命中明细 · 只读',
  entrySourcesEmpty: '暂无信源数据',
  metricTop1: 'Top1 占比',
  metricTop3: 'Top3 占比',
  metricTop5: 'Top5 占比',
  metricVisible: '可见占比',
  metricSource: '信源占比',
  industryLabel: '行业 P50',
  engineSelectAll: '全部',
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
  insightsBriefingMeta: (s: string, e: string) => `${s} → ${e}`,
  insightsRecommendations: '建议行动',
  insightsRecReason: '理由',
  queriesTitle: '监测报告',
  queriesSummary: (q: number, e: number) => `共 ${q} 条问题 · ${e} 个引擎`,
  queriesColQuery: '问题',
  queriesColHitRate: '命中率',
  queriesColRuns: '跑批数',
  queriesColHits: '命中数',
  queriesColFirstEngine: '首次命中引擎',
  queriesNeverHit: '尚未命中',
  queriesColIndex: '序号',
  queriesColSeed: '种子提示词',
  queriesColExpansion: '扩展提示词',
  queriesColHit: '命中',
  queriesColModel: '模型',
  queriesColAction: '查看',
  queriesNoSeed: '—',
  queriesFilterAllSeeds: '全部种子',
  queriesFilterAllModels: '全部模型',
  queriesEmptyAfterFilter: '当前筛选无匹配问题',
  queriesViewMatrix: '查矩阵 →',
  queriesViewDetail: '查看',
  queriesDetailEmpty: '暂无 AI 回答',
  queriesDetailHitBadge: '命中',
  queriesDetailMissBadge: '未命中',
  queriesDetailClose: '关闭',
  queriesDetailCitations: '引用来源',
  queriesDetailEnginesHeader: (n: number) => `各平台回答(${n})`,
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
  hintTopMetrics: '不重复的、至少被任意 1 个 AI 引擎引用过的监测问题数(分子);斜杠后的数字是当前 topic 监测问题总数(分母)。跟「引用总数」(URL 累计)不同',
  hintPublishedTotal: '当前主题下所有 status=published 的稿件数。不区分平台,审核通过并标记发布即计入',
  hintCitedTotal: '本期 AI 答复里**所有** citation URL 总和(URL 维度,SUM 答复里 citations 数组长度)。包括引到第三方 / 媒体 / 自有所有域名',
  hintRadar: '5 个维度叠在一张图上看品牌健康度:Top1/Top3/Top5 是品牌在 AI 答复中是第几位提到;可见 = 任意提到;信源占比 = 自有文章被 AI 引用的密度(分子为不去重的 owned-URL 引用条目,分母为命中响应去重 URL 数)',
  hintEntries: '点任意卡片进对应子页查看明细',
  hintCoreMetrics: '基于 LLM 抽取的 brand_rank(品牌在答复里第几个被提到)算占比。行业 P50 来自同行业 ≥3 个租户的中位数,样本不足时不展示',
  hintMetricTop1: '品牌在答复里排第 1 位被提到的格子占比。一个「格子」= 一个(监测问题 × 模型)组合。\n· 分子:品牌排第 1 位的格子数\n· 分母:监测问题数 × 模型数\n同一格子跑多次只算一次',
  hintMetricTop5: '品牌排前 5 位提到的格子占比。\n· 分子:品牌排名 ≤ 5 的格子数\n· 分母:监测问题数 × 模型数',
  hintMetricVisible: '品牌被任意提到过的格子占比(不看排名)。\n· 分子:答复里提到过品牌的格子数\n· 分母:监测问题数 × 模型数\n比 Top1 / Top5 更宽松',
  hintMetricSource: '自有文章被 AI 引用的密度。\n· 分子:所有响应里 citation 落在「已发布自有文章 URL」集合内的条目数(不去重,未命中响应里引了自有 URL 也算)\n· 分母:命中响应的 citation URL 归一化去重数\n比例可能 > 100%(分母去重 / 分子计次)。自有 URL = topic 下 status=published 的稿件 publish_targets 里的 url',
  hintBriefings: '本主题的监测问题命中明细 — 每条问题在哪些引擎上命中过。点条目看具体答复',
  hintPublished: '已发布的稿件清单。点 chip 跳外站;有"AI 引用"绿标表示该投放 URL 出现在 AI 答复的引用清单里',
  hintPublishedCited: '该 publish_targets[].url 出现在 Response.citations_json 中,按引擎分组展示被引擎引用的次数',
  hintSources: 'AI 答复里出现过的 citation domain 排序。Top 7 单独成色块,其余合并到「其它」桶',
  hintSourcesStat: '总引用 = 周期内所有答复的引用条数累加;唯一域名数 ≤ 10(后端只返回 Top 10);自有占比 = owned / total × 100',
  hintSourcesUnique: '本期出现过的不同引用域名数。上限是后端返回的 Top 10 — 实际可能更多但只算 Top 10 内',
  hintSourcesOwnedPct: '权威媒体引用 / 总引用 × 100。比例越高说明 AI 答复越倾向引用品牌自家域名',
  hintSourcesFilter: '切换看全部 / 仅自有权威域名 / 仅第三方域名。自有判定 = 域名子串包含品牌关键词',
  hintSourcesDomainEngine: '同一域名在不同 AI 引擎间的引用拆分。条形越长 = 该引擎引用该域名越多。点行查看引用样本',
  hintEngines: '不同 AI 引擎对品牌相关 query 的引用频次(SUM 该引擎所有 response 的 citations 长度)',
  hintEnginesOverview: '每个引擎在本期跑批的引用总数。点卡片跳到该引擎的原始答复列表',
  hintEnginesHeatmap: '行 = 引擎,列 = Top 12 域名。颜色越深表示该引擎越依赖该 domain(归一化到该 domain 全局总数)。空白 = 0 次',
  hintCompetitors: 'SAIV = 品牌提及次数 / (品牌 + 全部竞品提及总次数) × 100。竞品提及来自 LLM 抽出的 competitors_json',
  hintCompetitorsTop: '本期被 LLM 抽出最多的竞品 Top 10,按提及次数降序。点 chip 可过滤下方「被替代证据」表',
  hintCompetitorsPosition: '命中 hit=True 的答复里,品牌出现的段落位置:lead=首段,body=中段,tail=末段,unknown=后处理未定位',
  hintCompetitorsSubs: '"提了竞品但没提你"的 query 清单:hit=False 但 competitors_json 非空的答复,按 (query × 竞品) 聚合次数,展示证据 snippet',
  hintMatrix: '每个 (问题 × 引擎) cell 的累计命中状态。深绿 = Top1,中绿 = Top3,浅绿 = Top5,极浅绿 = 命中但 rank 未抽出,灰 = 未命中,占位灰底 = 还没跑过。点 cell 看历次答复',
  hintMatrixTimeline: '每个引擎在所有 query 里最早一次首命中的日期,显示「上线后第 X 天」。X = (first_hit_at − topic.created_at).days',
  hintInsights: '左侧周报列表,点新生成手动触发上一自然周的周报;每条选中后右侧展示正文 + 建议',
  hintInsightsView: '周报正文(LLM 生成 markdown) + 优先级标记的建议行动列表。下方可对周报质量打分 1-5',
  hintQueries: '所有监测问题的累计命中状况,只读 — 编辑 / 新增问题在 admin 工作台',
  hintQueriesTable: '每行 = 1 个监测问题。✓ 是二元状态(命中过 / 没命中过),只要任一引擎累计命中数 ≥ 1 就亮,不看命中率。模型列展开所有命中过的引擎',
  hintResponses: '原始 AI 答复流。chip 含命中状态(Top<n> / 未命中)、段落位置(开头 / 中段 / 末尾)、引用清单条数。展开看全文 + 引用列表',
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
  metricCitations: 'Total Recommendations',
  metricPublishedTotal: 'Published',
  metricCitedTotal: 'Cited by AI',
  blockRadar: 'Trend (5-dim breakdown)',
  blockEntries: 'Modules',
  blockCoreMetrics: 'Core Metrics',
  blockBriefings: 'Briefings',
  blockPublished: 'Published Content',
  emptyBriefings: 'No tracked queries yet — add some in admin workbench',
  emptyPublished: 'No published content',
  entrySources: 'Sources',
  entryEngines: 'Engines',
  entryCompetitors: 'Competitors',
  entryMatrix: 'Query Matrix',
  entryInsights: 'Insights',
  entryQueries: 'Tracking Report',
  entryEnginesSub: 'engines covered',
  entryCompetitorsSub: 'rivals + substitution evidence →',
  entryMatrixSub: 'query × engine hit detail →',
  entryInsightsSub: 'briefings + recommendations',
  entryQueriesSub: 'per-query hit detail · read-only',
  entrySourcesEmpty: 'no source data yet',
  metricTop1: 'Top-1 share',
  metricTop3: 'Top-3 share',
  metricTop5: 'Top-5 share',
  metricVisible: 'Visibility',
  metricSource: 'Source share',
  industryLabel: 'Industry P50',
  engineSelectAll: 'All',
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
  insightsBriefingMeta: (s, e) => `${s} → ${e}`,
  insightsRecommendations: 'Recommendations',
  insightsRecReason: 'Reason',
  queriesTitle: 'Tracking Report',
  queriesSummary: (q, e) => `${q} queries · ${e} engines`,
  queriesColQuery: 'Query',
  queriesColHitRate: 'Hit rate',
  queriesColRuns: 'Runs',
  queriesColHits: 'Hits',
  queriesColFirstEngine: 'First-hit engine',
  queriesNeverHit: 'never hit',
  queriesColIndex: '#',
  queriesColSeed: 'Seed prompt',
  queriesColExpansion: 'Expanded query',
  queriesColHit: 'Hits',
  queriesColModel: 'Model',
  queriesColAction: 'View',
  queriesNoSeed: '—',
  queriesFilterAllSeeds: 'All seeds',
  queriesFilterAllModels: 'All models',
  queriesEmptyAfterFilter: 'No queries match the current filter',
  queriesViewMatrix: 'See matrix →',
  queriesViewDetail: 'View',
  queriesDetailEmpty: 'No AI responses',
  queriesDetailHitBadge: 'Hit',
  queriesDetailMissBadge: 'Miss',
  queriesDetailClose: 'Close',
  queriesDetailCitations: 'Citations',
  queriesDetailEnginesHeader: (n: number) => `Responses across engines (${n})`,
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
  hintTopMetrics: 'Number of distinct tracked queries where at least one AI engine has mentioned the brand (numerator). Denominator is total tracked queries for this topic. Different from "Total Citations" (URL accumulation)',
  hintPublishedTotal: 'Count of docs with status=published under the current topic. Platform-agnostic; counts every approved + published doc',
  hintCitedTotal: 'Total citation URLs across all AI responses this period (URL-level, SUM of citations[]). Includes citations to third-party / media / owned domains alike',
  hintRadar: '5 dimensions in one view. Top1/Top3/Top5 = brand position in the answer; Visible = any mention; Source share = density of AI citations landing on your published articles (occurrence count over deduplicated hit-citation URLs)',
  hintEntries: 'Click any card to drill into the sub-page',
  hintCoreMetrics: 'Based on LLM-extracted brand_rank. Industry P50 = median across ≥3 same-industry tenants; hidden when sample too small',
  hintMetricTop1: 'Share of cells where the brand is the 1st-mentioned. A "cell" = one (query × model) combination.\n· Numerator: cells where brand ranks #1\n· Denominator: #queries × #models\nOne cell counts at most once across reruns',
  hintMetricTop5: 'Share of cells where the brand ranks in the top 5.\n· Numerator: cells with brand rank ≤ 5\n· Denominator: #queries × #models',
  hintMetricVisible: 'Share of cells where the brand was ever mentioned (any rank).\n· Numerator: cells that mentioned the brand at least once\n· Denominator: #queries × #models\nLooser than Top1 / Top5',
  hintMetricSource: 'How densely AI citations land on the brand\'s own published articles.\n· Numerator: citation entries whose URL falls in the "published owned-articles" set, across ALL responses (not deduped; includes hit=False responses that cited an owned URL)\n· Denominator: deduplicated citation URLs from hit responses\nCan exceed 100% (denominator dedupes, numerator counts occurrences). Owned URLs = publish_targets[].url of docs with status=published',
  hintBriefings: 'Per-query hit detail for this topic — which engines have hit each query. Click a row to view the answers',
  hintPublished: 'Published docs. Chips link to external posts; the green "cited by" badge means the URL appears in an AI answer\'s citation list',
  hintPublishedCited: 'The publish_targets[].url appears in Response.citations_json; chips show per-engine cite count',
  hintSources: 'Domains cited in AI answers, ranked by count. Top 7 are colored individually; the rest collapse into "Other"',
  hintSourcesStat: 'Total citations = SUM of citations across all responses. Unique domains ≤ 10 (backend returns Top 10 only). Owned share = owned / total × 100',
  hintSourcesUnique: 'Distinct domains cited this period. Capped at backend\'s Top 10 — real count may be higher but only Top 10 is exposed',
  hintSourcesOwnedPct: 'Authoritative citations / total citations × 100. Higher = AI answers favor citing your own domains',
  hintSourcesFilter: 'Toggle all / owned-only / third-party-only. Owned = domain substring contains a brand keyword',
  hintSourcesDomainEngine: 'How each domain is cited across engines. Bar length per engine = its share. Click a row to view sample citations',
  hintEngines: 'Citation frequency per AI engine (SUM of citations from that engine\'s responses)',
  hintEnginesOverview: 'Per-engine total citations this period. Click a card to jump to that engine\'s raw responses',
  hintEnginesHeatmap: 'Rows = engines, columns = Top 12 domains. Darker = engine more reliant on that domain (normalized to the domain\'s global total). Blank = 0',
  hintCompetitors: 'SAIV = brand mention count / (brand + sum of competitor mentions) × 100. Competitor mentions come from LLM-extracted competitors_json',
  hintCompetitorsTop: 'Top 10 LLM-extracted competitors this period, sorted by mention count. Click a chip to filter the "Substitution evidence" table below',
  hintCompetitorsPosition: 'For hit=True responses, where the brand was mentioned: lead=first paragraph, body=middle, tail=end, unknown=post-processing couldn\'t locate',
  hintCompetitorsSubs: '"Mentioned the rival but not you" queries: responses with hit=False but competitors_json populated, grouped by (query × rival) with an evidence snippet',
  hintMatrix: 'Hit state per (query × engine). Deep green = Top1, mid green = Top3, light green = Top5, very light green = hit but rank unknown, gray = miss, placeholder = not yet run. Click a cell for history',
  hintMatrixTimeline: 'For each engine, the earliest first_hit_at across all queries, shown as "day X after topic creation". X = (first_hit_at − topic.created_at).days',
  hintInsights: 'Briefings on the left; click Generate to manually trigger for the last natural week. Selecting one shows body + recommendations on the right',
  hintInsightsView: 'LLM-generated markdown body + prioritized action items. You can rate the briefing 1-5 below',
  hintQueries: 'All tracked queries with cumulative hit status (read-only). Add / edit queries in admin workbench',
  hintQueriesTable: 'One row per query. ✓ is binary (ever-hit vs never-hit) — lights up as long as at least one engine has any hit, regardless of hit rate. The Model column lists every engine that has hit this query',
  hintResponses: 'Raw AI answer stream. Chips show hit state (Top<n> / miss), mention position (lead / body / tail), citation count. Expand to view full text + citation list',
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
