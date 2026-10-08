// Report center i18n dictionary — local dictionary mode, leaving the global zh.ts untouched.

import { useTranslation } from 'react-i18next';
import type { BasisChange, BasisChangeKind, MetricKey } from '../../services/reportsApi';
import { engineLabel } from '../../constants/engines';

interface Dict {
  // Module shell
  moduleTitle: string;
  navOverview: string;
  navMetrics: string;
  navCompliance: string;
  topicLabel: string;
  backToList: string;
  // Brand tags (benchmark) — shown read-only in the report center, set in step one of topic settings
  brandTagLabel: string;
  hintBrandTag: string;
  tagUnset: string;
  brandTagUnset: string;          // Placeholder text when no tag is set
  brandTagMetricsIntro: string;   // First line of the exclamation-mark hint
  brandTagMetricsOutro: string;   // Last line of the exclamation-mark hint (pointing to where to change it)
  brandTagMetricHeader: string;   // Header of the "Metric" column in the range table
  tierLabels: Record<'new' | 'mid' | 'leader', string>;
  industryClassLabels: Record<'regular' | 'special', string>;
  // Overview (summary report)
  overviewBanner: (met: number, total: number) => string;
  overviewBannerHint: string;
  overviewTrendTitle: string;
  latestPrefix: string;
  viewAllList: string;
  openReport: string;
  noTopicTitle: string;
  noTopicHint: string;

  loading: string;
  empty: string;
  view: string;
  exportPdf: string;
  exportClosing: string;
  exporting: string;
  prevPeriod: string;
  nextPeriod: string;
  backToCenter: string;

  // Five metrics
  metricLabels: Record<MetricKey, string>;
  metricHints: Record<MetricKey, string>;
  hintTarget: string;
  hintDailyDetail: string;
  hintBeforeAfter: string;
  zeroToSome: string;
  hintPlatformMatrix: string;
  hintWeeklyPlatformMatrix: string;
  hintTrendForecast: string;
  hintCorrections: string;
  hintWeeklyCorrections: string;
  hintPositionDist: string;
  hintCitationSources: string;
  hintOverviewBanner: string;
  hintOverviewTrend: string;
  // Metric definition change markers (vertical lines on the trend chart + note bar below)
  basisChangeFlag: string;
  basisChangeTitle: string;
  basisChangeHint: string;
  basisChangeKinds: Record<BasisChangeKind, string>;
  basisChangeDesc: (c: BasisChange) => string;
  trendNumDen: (num: number, den: number) => string;
  baseline: string;
  target: string;
  progressMet: string;
  progressNear: string;
  progressMissed: string;
  noTarget: string;
  vsPrev: string;
  firstPeriod: string;

  // Report center
  centerTitle: string;
  tabDaily: string;
  tabWeekly: string;
  tabMonthly: string;
  colReport: string;
  colKpiSummary: string;
  colResponses: string;
  colOps: string;
  dailyPrefix: string;
  weeklyPrefix: string;
  monthlyPrefix: string;
  emptyReports: string;

  // Daily report
  dailyTitle: string;
  dailyScope: (words: number, engines: number, rounds: number, responses: number) => string;
  secAiSummary: string;
  aiSummaryHint: string;
  secKpiDiff: string;
  secHighlights: string;
  colRound: string;
  secActions: string;
  secDetail: string;
  colMetric: string;
  colYesterday: string;
  colToday: string;
  colDelta: string;
  colNote: string;
  hlRounds: (n: number, m: number) => string;
  hlYesterday: string;
  hlNew: string;
  hlUp: string;
  hlDown: string;
  detailSummary: (n: number) => string;
  noChangeToday: string;
  flatCollapsed: (n: number) => string;
  moreChanged: (n: number) => string;
  colTodayRank: string;
  colChange: string;
  colActionTime: string;
  colActionTitle: string;
  emptyActions: string;
  colQuery: string;
  colEngine: string;
  colHits: string;
  hintHits: string;
  rankMissing: string;
  hintRankMissing: string;
  colExposed: string;
  colRank: string;
  colSentiment: string;
  colCitedOwn: string;
  filterChangedOnly: string;
  exposed: string;
  notExposed: string;
  yes: string;
  no: string;
  sentimentLabels: Record<string, string>;
  rankBadge: Record<string, string>;
  detailCount: (n: number) => string;

  // Weekly report
  weeklyTitle: string;
  colBaseline: string;
  colTarget: string;
  colProgress: string;
  emptyBody: string;

  // Monthly report
  monthlyTitle: string;
  verdictMet: string;
  verdictPartial: string;
  verdictMissed: string;
  secKpiCards: string;
  // The four sections added to the weekly report (same as the monthly report, titles without numbering -- the weekly report itself isn't numbered)
  hintWeeklyBeforeAfter: string;
  secPlatformMatrix: string;
  secTrend: string;
  secCorrections: string;
  secDeliverables: string;
  secNextPlan: string;
  before: string;
  after: string;
  relative: string;
  metBadge: string;
  colIssueType: string;
  colFound: string;
  colStatus: string;
  colAction: string;
  emptyCorrections: string;
  forecastLegend: string;
  historyLegend: string;

  // Metrics board
  boardTitle: string;
  secPositionDist: string;
  secCitationSources: string;
  sceneLabels: Record<string, string>;
  distTop1: string;
  distTop23: string;
  dist410: string;
  distMiss: string;
  distExposed: string;
  colSourceTitle: string;
  colSourceType: string;
  colSourceEngines: string;
  colSourceCount: string;
  colSourceOwned: string;
  queryCount: (n: number) => string;
  totalCitations: (n: number) => string;
  totalCitationsShort: string;
  srcBarLegend: string;

  // Competitor comparison analysis
  secCompetitive: string;
  secContrast: string;
  hintContrast: string;
  secInsight: string;
  hintInsight: string;
  secHeat: string;
  hintHeat: string;
  secRanking: string;
  hintRanking: string;
  kwCoverage: string;
  hintKwCoverage: string;
  prodRank: string;
  hintProdRank: string;
  hintProdMention: string;
  prodMentionShort: string;
  rankUnit: string;
  rankPrefix: string;
  cmpOurs: string;
  cmpTop: string;
  cmpIndustry: string;
  vsPrevShort: string;
  industryAvg: string;
  gapTop2: string;
  hintGapTop2: string;
  targetProgress: string;
  vsIndustryPp: (diff: number) => string;
  vsIndustryRank: (diff: number) => string;
  improveBtn: string;
  optimizeKw: string;
  hintOptimizeKw: string;
  getBtn: string;
  actionRailTitle: string;
  heatLow: string;
  heatHigh: string;
  heatWeak: string;
  heatStrong: string;
  heatRankCol: string;
  heatKwCol: string;
  heatValCol: string;
  // Dual metric definition heat map (2026-08): on-site hit heat + external search heat
  heatSourceLabels: Record<string, string>;
  heatCoreWord: string;
  heatNoSearch: string;
  heatStaleTag: string;
  heatRefresh: string;
  heatRefreshing: string;
  heatCollected: (fresh: number, total: number) => string;
  heatRefreshDone: (written: number) => string;
  heatRefreshSkipped: (sources: string) => string;
  heatRefreshFailed: string;
  heatTooltip: (search: string) => string;
  rankingRankCol: string;
  rankingProductCol: string;
  compositeCol: string;
  hintComposite: string;
  actionCol: string;
  boostBtn: string;
  brandSelf: string;
  monitorTarget: string;
  relatedComp: string;
  rankHeatHint: string;
  viewFullCompetitive: string;
  cmpVerdict: (name: string, lead: number, total: number) => string;
  leadBadge: string;
  trailBadge: string;
  rankValue: (n: number) => string;

  // Reused by report list pagination (ReportList) + industry search box (IndustryDetail)
  searchPlaceholder: string;
  pagePrev: string;
  pageNext: string;
}

const ZH: Dict = {
  moduleTitle: '报告中心',
  navOverview: '报告总览',
  navMetrics: '指标看板',
  navCompliance: '合规审计',
  topicLabel: '主题',
  backToList: '返回列表',
  brandTagLabel: '品牌标签',
  hintBrandTag: '给自己账号的品牌打上层级与行业标签,六项指标卡的目标会按同层级参考区间设置(达到区间下限即达标);未打标签时目标为基线 × 1.3。',
  tagUnset: '未设置',
  brandTagUnset: '未设置(去主题设置第一步)',
  brandTagMetricsIntro: '不同品牌层级的六项指标目标参考区间(达到区间下限即达标):',
  brandTagMetricsOutro: '未设置标签时,目标 = 基线 × 1.3。如需修改品牌标签,请到「主题设置」第一步。',
  brandTagMetricHeader: '指标',
  tierLabels: { new: '新品牌', mid: '中等品牌', leader: '行业头部' },
  industryClassLabels: { regular: '常规行业', special: '特殊行业(医疗/美容/金融)' },
  overviewBanner: (met, total) => `${total} 项有目标指标中 ${met} 项已达标(≥3 项达标视为整体达标)`,
  overviewBannerHint: '已打品牌层级标签:目标 = 层级参考区间下限;未打标签:目标 = 基线 × 1.3。按周口径评估',
  overviewTrendTitle: '整体露出率日度趋势(近 30 期)',
  latestPrefix: '最新',
  viewAllList: '全部',
  openReport: '查看报告',
  noTopicTitle: '还没有任何监测主题',
  noTopicHint: '监测主题由管理员配置,配置并跑批后这里会自动生成日报/周报/月报。',

  loading: '加载中…',
  empty: '暂无数据',
  view: '查看',
  exportPdf: '导出 PDF',
  exportClosing: '导出结案报告',
  exporting: '生成中…',
  prevPeriod: '上一期',
  nextPeriod: '下一期',
  backToCenter: '报告中心',

  metricLabels: {
    exposure: '整体露出率', top1: 'TOP1露出率', top3: 'TOP3露出率',
    accuracy: '内容中正率', citation: '文章引用率', mention: '品牌声量度',
  },
  metricHints: {
    exposure: '本品在「提示词 × 平台」组合里的露出覆盖面。'
      + '\n一个组合 = 一个提示词 × 一个平台;本期内跑了几轮、同一组合只算一次,命中取「本期命中过」。'
      + '\n分子 = 命中过的品类词组合数;分母 = 本期覆盖到的品类词组合数(剔除报错)。'
      + '\n若该主题设了分母设定值,分母钉死在该值上(与本期实跑多少无关),顶穿的部分按 100% 显示。',
    top1: '品类词组合里,本品做到推荐首位的比例。\n位次取该组合本期内的最佳一轮;衡量「AI 第一个推荐的是不是我」。'
      + '\n设了分母设定值时,分母与整体露出率同尺(三个露出率并排可比)。',
    top3: '品类词组合里,本品进入前 3 位的比例(含 TOP1)。\n位次取本期内的最佳一轮。'
      + '\n设了分母设定值时,分母与整体露出率同尺。',
    accuracy: '命中应答中,表述无负面/偏差的比例。\n分母 = 命中应答数 —— 按应答条数算,不按组合去重(问的是「AI 说错了多少次」)。'
      + '\n落在「负面提及」判定窗口内的命中应答计为不中正,复检回正后不再计负。衡量 AI 说得对不对、正不正。',
    citation: '引用了本品自有内容(已发布文章 URL)的组合占比。\n分母 = 本期覆盖到的全部「提示词 × 平台」组合数(含品牌词),同一组合本期引用过就算。'
      + '\n衡量我们供给的内容有没有被 AI 采信引用;设了分母设定值时分母钉死在该值上。',
    mention: 'AI 回答里品牌被提及的次数,占品牌与竞品总提及量的份额。\n按应答条数累计,不按组合去重(问的是「被说了多少次」)。'
      + '\n分子:默认按全部命中应答算;该主题开了「声量只算品类词」开关时,只算品类词命中'
      + '(问品牌名的问题 AI 必然提到品牌,算进去等于自己给自己送分)。分母 = 品牌命中 + 竞品提及。'
      + '\n竞品提及数来自跑批后的抽取,抽取没跑时分母只剩品牌自己 → 该项会虚高到 100%。'
      + '\n衡量在同一批回答里,声音有多少属于我们;打了品牌层级标签时按层级区间设目标并参与达标,未打标签不设目标。',
  },
  hintTarget: '已打品牌层级标签的主题:目标 = 该层级参考区间,达到区间下限即达标。\n未打标签的主题:目标 = 基线 × 1.3(合同口径:提升 ≥30%);基线取首个数据周期的值,基线为 0 时该项不设目标。',
  hintDailyDetail: '一行 = 一个词 × 平台。\n排名取当日多轮采样中的最佳轮;N/M = 命中轮数/采样轮数(衡量稳定性);变化为与昨日最佳状态对比,有变化的行排在前面。',
  hintBeforeAfter: '优化前 = 上月最后一个自然周(首月为全周期首个自然周);优化后 = 本月最后一个自然周。\n相对提升 = (后 - 前) / 前 × 100%,≥30% 打达标勾;优化前为 0 且优化后 > 0 视为从无到有,直接达标。',
  zeroToSome: '从无到有',
  hintPlatformMatrix: '本月各平台六项指标全量表现;同一列内颜色越深表现越好。',
  hintWeeklyPlatformMatrix: '本周各平台六项指标全量表现;同一列内颜色越深表现越好。',
  hintTrendForecast: '实线为历史周度整体露出率;虚线为按当前斜率线性外推的未来 4 周预测。',
  hintCorrections: '负面/偏差应答的发现 → 处理 → 复检记录,以及全月未露出词的攻坚清单。',
  hintWeeklyCorrections: '负面/偏差应答的发现 → 处理 → 复检记录,以及本周未露出词的攻坚清单。',
  hintPositionDist: '一个组合 = 一个提示词 × 一个平台(如「智算中心哪家好 × 豆包」)。\n色块内的数字 = 该词类下处于这个位次段的组合个数,按历史最佳位次分档:\nTOP1(曾做到首位)/ TOP2-3 / 4-10位 / 未露出(从未被提及)。\n品牌词和竞品对比词的问题本身点名品牌,位次没有意义,按 TOP1 / 露出 / 未露出 三档展示。\n例:品牌词 6 个提示词 × 5 平台 = 30 个组合,TOP1 段标 21 即 21 个组合曾进首位。',
  hintCitationSources: 'AI 应答引用的信源聚合:左侧为类型与自有构成,右侧按平台聚合展示被引用最多的 10 个平台。\n类型按域名与标题判定(权威榜单/评价/社交/官方等);「自有」= 本品发布的内容被引用。',
  hintOverviewBanner: '有目标的指标中至少 3 项达标,视为整体达标;按周口径评估。\n已打品牌层级标签的主题:目标 = 层级参考区间下限;未打标签:目标 = 基线 × 1.3(合同口径:提升 ≥30%)。',
  hintOverviewTrend: '按日聚合的整体露出率(近 30 期),反映内容供给 → 收录 → 露出的传导趋势。',
  basisChangeFlag: '口径变更',
  basisChangeTitle: '口径变更说明',
  basisChangeHint: '竖线处统计口径变了(提示词增减、引擎接入或停跑、计算规则调整),'
    + '两侧的分母基数不同,曲线高低不宜直接比较 —— 这里的涨跌未必来自优化效果。',
  basisChangeKinds: {
    queries: '提示词词表变更',
    scope: '计算口径调整',
    engines: '引擎集变更',
    denom: '样本基数跳变',
  },
  basisChangeDesc: c => {
    if (c.kind === 'engines') {
      const parts: string[] = [];
      if (c.engines_added.length) parts.push(`新增 ${c.engines_added.map(engineLabel).join('、')}`);
      if (c.engines_removed.length) parts.push(`停跑 ${c.engines_removed.map(engineLabel).join('、')}`);
      return parts.join(';');
    }
    if (c.before === null || c.after === null) {
      return c.after !== null ? `调整为 ${c.after}` : '统计规则调整,与更早的历史不同口径';
    }
    const pct = c.before > 0 ? Math.round(((c.after - c.before) / c.before) * 100) : null;
    const noun = c.kind === 'queries' ? '提示词' : '有效应答';
    const delta = pct === null ? '' : `(分母 ${pct >= 0 ? '+' : ''}${pct}%)`;
    return `${noun} ${c.before} → ${c.after}${delta}`;
  },
  trendNumDen: (num, den) => `命中 ${num} / 组合 ${den}`,
  baseline: '基线',
  target: '目标',
  progressMet: '已达标',
  progressNear: '接近',
  progressMissed: '未达标',
  noTarget: '—',
  vsPrev: '环比',
  firstPeriod: '首期基线',

  centerTitle: '报告中心',
  tabDaily: '日报',
  tabWeekly: '周报',
  tabMonthly: '月报',
  colReport: '报告',
  colKpiSummary: 'KPI 摘要',
  colResponses: '应答数',
  colOps: '操作',
  dailyPrefix: '日报',
  weeklyPrefix: '周报',
  monthlyPrefix: '月报',
  emptyReports: '该周期暂无跑批数据,开启每日监测后自动生成',

  dailyTitle: '日报',
  dailyScope: (w, e, r, n) => `${w}词 × ${e}平台 × ${r}轮采样 · 本日有效应答 ${n} 次`,
  secAiSummary: 'AI 日报总结',
  aiSummaryHint: '由系统每日自动分析昨日指标变化与命中情况生成;未生成时不展示本卡片。',
  secKpiDiff: '① 指标日环比',
  secHighlights: '② 当日明细(词 × 平台)',
  colRound: '采样轮次',
  secActions: '③ 今日动作',
  secDetail: '④ 全量明细',
  colMetric: '指标',
  colYesterday: '昨日',
  colToday: '今日',
  colDelta: '日环比',
  colNote: '备注',
  hlRounds: (n, m) => `${n}/${m} 轮命中`,
  hlYesterday: '昨日',
  hlNew: '新增',
  hlUp: '上升',
  hlDown: '下降',
  detailSummary: n => `今日共 ${n} 个 词×平台 组合`,
  noChangeToday: '今日各词×平台状态与昨日持平,无升降变化',
  flatCollapsed: n => `另有 ${n} 项与昨日持平,点击展开`,
  moreChanged: n => `展开其余 ${n} 项变化`,
  colTodayRank: '今日排名/露出',
  colChange: '变化',
  colActionTime: '时间',
  colActionTitle: '动作',
  emptyActions: '今日无内容动作',
  colQuery: '关键词',
  colEngine: '引擎',
  colHits: '命中 / 应答',
  hintHits: '本期该平台的品类词命中次数 / 应答次数(真实观测数)。\n命中 ÷ 应答 = 右边的整体露出率;设了分母设定值时,分母显示的是钉死后的展示口径(设定值 ÷ 模型数)。',
  rankMissing: '未采集',
  hintRankMissing: '本期这个平台一条位次都没采集到,所以不显示 TOP1/TOP3。\n位次由跑批后的自动分析写入,分析中断时会整段为空 —— 这与「排不进前列」是两回事,故不显示成 0%。',
  colExposed: '露出',
  colRank: '排名',
  colSentiment: '倾向',
  colCitedOwn: '引用本品',
  filterChangedOnly: '仅看变化',
  exposed: '露出',
  notExposed: '未露出',
  yes: '是',
  no: '否',
  sentimentLabels: { positive: '正面', neutral: '中性', negative: '负面', unknown: '—' },
  detailCount: n => `共 ${n} 条`,

  weeklyTitle: '周报',
  colBaseline: '基线',
  colTarget: '目标(+30%)',
  colProgress: '进度',
  emptyBody: '该周未生成 AI 解读(周报正文),数字指标不受影响',

  monthlyTitle: '月报',
  verdictMet: 'KPI 达标',
  verdictPartial: 'KPI 部分达标',
  verdictMissed: 'KPI 未达标',
  secKpiCards: '① KPI 总览(优化前 → 优化后)',
  hintWeeklyBeforeAfter: '优化前 = 基线周(跟踪起点的那一周);优化后 = 本周。\n相对提升 = (后 - 前) / 前 × 100%,≥30% 打达标勾;优化前为 0 且优化后 > 0 视为从无到有,直接达标。',
  secPlatformMatrix: '② 分平台 × 六项指标',
  secTrend: '③ 整体露出率周度趋势与预测',
  secCorrections: '④ 纠偏与攻坚记录',
  secDeliverables: '⑤ 本期交付',
  secNextPlan: '⑥ 下期计划',
  before: '优化前',
  after: '优化后',
  relative: '相对提升',
  metBadge: '达标',
  colIssueType: '问题类型',
  colFound: '发现',
  colStatus: '状态',
  colAction: '处理方式',
  emptyCorrections: '本期无负面纠偏与未露出攻坚项',
  forecastLegend: '预测(线性外推)',
  historyLegend: '历史',

  boardTitle: '指标看板',
  secPositionDist: '位次分布(按词类)',
  secCitationSources: '引用信源分析',
  sceneLabels: {
    compare: '竞品对比词', brand: '品牌词', search: '搜索词', qa: '问答词', intent: '意图词',
    scenario: '场景词', unknown: '未分类',
  },
  rankBadge: { top1: 'TOP1', top3: 'TOP3', r4_10: '4-10位', exposed: '露出', miss: '未露出', nodata: '无数据' },
  distTop1: 'TOP1',
  distTop23: 'TOP2-3',
  dist410: '4-10位',
  distMiss: '未露出',
  distExposed: '露出',
  colSourceTitle: '被引信源',
  colSourceType: '类型',
  colSourceEngines: '被引引擎',
  colSourceCount: '引用次数',
  colSourceOwned: '自有',
  queryCount: n => `${n} 词`,
  totalCitations: n => `累计引用 ${n} 次`,
  totalCitationsShort: '累计引用',
  srcBarLegend: '仅展示引用最多的 10 个平台',

  secCompetitive: '竞品对比分析',
  secContrast: '竞品对比数据',
  hintContrast: '行业平均 = 品牌与全部竞品的均值;覆盖率/声量/位次三项指标对比品牌、各竞品与行业均值(位次已反向归一,越靠前得分越高)。总览雷达图另叠加内容中正率与文章引用率两维:本品牌为真实值(与顶部指标卡同口径);竞品这两项无法直接观测,按其露出层级取行业参考区间的估计值。总览用雷达图叠加各方轮廓,报告内用对比条。口径取最新一周,与顶部指标卡一致。',
  secInsight: '市场竞争洞察',
  hintInsight: '与头部竞品的覆盖差距,以及尚未命中的机会关键词数量。',
  secHeat: '关键词热量图',
  hintHeat: '「搜索热度」= 这个词在真实搜索里的需求强度,来自外部数据源(百度指数 / 谷歌趋势 / 巨量算数),外部源查不到的长尾词用大模型按行业上下文估算,每块都标注来源;色块面积与颜色深浅同步代表热度高低,越大越深越热。搜索热度是缓存值,由定时任务续采,可点「刷新」立即重采。',
  secRanking: '产品排行榜',
  hintRanking: '品牌与竞品在各 AI 平台的露出率得分(0-100):品牌分=整体露出率(与顶部指标卡同口径);竞品分=同答案出现率。综合分数=窗口全量口径,与竞品对比数据是同一个数;各平台列为分平台拆解。',
  kwCoverage: '整体露出率',
  hintKwCoverage: '品牌 = 窗口内命中应答占有效应答的比例(与顶部指标卡整体露出率同口径);竞品 = 窗口内出现该竞品的应答比例。',
  prodRank: '产品排名',
  hintProdRank: '产品排行榜的组内名次:品牌与竞品按各平台露出率得分的综合分统一排序,与下方排行榜一致,数值越小越靠前。',
  hintProdMention: '同一批 AI 回答中,本品牌被提及的次数占「品牌 + 全部竞品」总提及次数的比例。\n分子 = 品牌被提及次数;分母 = 品牌与竞品提及次数之和。\n数值越高,代表在同类话题的 AI 回答里,本品牌占据的话语份额越大。',
  prodMentionShort: '品牌声量度',
  rankUnit: '位',
  rankPrefix: '第',
  cmpOurs: '我方',
  cmpTop: '头部',
  cmpIndustry: '行均',
  vsPrevShort: '环比',
  industryAvg: '行业平均',
  gapTop2: '与头部竞品露出差距',
  hintGapTop2: '我方整体露出率减去出现率最高竞品的出现率,正值代表领先。',
  targetProgress: '目标进度',
  vsIndustryPp: diff => diff >= 0
    ? `高于行业平均 ${diff} 个百分点`
    : `低于行业平均 ${Math.abs(diff)} 个百分点`,
  vsIndustryRank: diff => diff > 0
    ? `领先行业平均 ${diff} 位`
    : diff < 0 ? `落后行业平均 ${Math.abs(diff)} 位` : '与行业平均持平',
  improveBtn: '提 升',
  optimizeKw: '待优化关键词',
  hintOptimizeKw: '窗口内一次都未命中的提示词数量,即机会关键词。',
  getBtn: '获 取',
  actionRailTitle: '行动建议',
  heatLow: '低',
  heatHigh: '高',
  heatWeak: '弱',
  heatStrong: '强',
  heatRankCol: '排名',
  heatKwCol: '关键词名称',
  heatValCol: '热度值',
  heatSourceLabels: {
    baidu_index: '百度指数',
    google_trends: '谷歌趋势',
    oceanengine: '巨量算数',
    llm: '模型估算',
  },
  heatCoreWord: '实际查询词',
  heatNoSearch: '待采集',
  heatStaleTag: '数据偏旧',
  heatRefresh: '刷新',
  heatRefreshing: '采集中…',
  heatCollected: (fresh, total) => `搜索热度已采 ${fresh}/${total}`,
  heatRefreshDone: written => `已更新 ${written} 个词的搜索热度`,
  heatRefreshSkipped: sources => `以下来源本轮没出数:\n${sources}`,
  heatRefreshFailed: '搜索热度采集失败,请稍后再试',
  heatTooltip: search => `搜索热度 ${search}`,
  rankingRankCol: '排名',
  rankingProductCol: '产品/品牌',
  compositeCol: '综合分数',
  hintComposite: '窗口全量应答的露出/出现率,与竞品对比数据、顶部指标卡是同一个数。',
  actionCol: '操作',
  boostBtn: '提升排名',
  brandSelf: '本品牌',
  monitorTarget: '监测对象',
  relatedComp: '相关竞品',
  rankHeatHint: '格子深浅=各平台露出率得分',
  viewFullCompetitive: '查看完整竞品分析',
  cmpVerdict: (name, lead, total) => `本期对阵「${name}」:${total} 项核心指标领先 ${lead} 项`,
  leadBadge: '领先',
  trailBadge: '落后',
  rankValue: n => (n > 0 ? `第${n}位` : '—'),

  searchPlaceholder: '搜索关键词…',
  pagePrev: '上一页',
  pageNext: '下一页',
};

const EN: Dict = {
  moduleTitle: 'Report center',
  navOverview: 'Overview',
  navMetrics: 'Metrics board',
  navCompliance: 'Compliance',
  topicLabel: 'Topic',
  backToList: 'Back to list',
  brandTagLabel: 'Brand tags',
  hintBrandTag: 'Tag your own brand with a tier and industry label; targets on the six KPI cards follow the tier reference range (reaching the lower bound counts as met). Untagged topics keep target = baseline × 1.3.',
  tagUnset: 'Not set',
  brandTagUnset: 'Not set (see topic settings step 1)',
  brandTagMetricsIntro: 'Target reference bands for the six metrics by brand tier (reaching the lower bound counts as met):',
  brandTagMetricsOutro: 'Without tags, target = baseline × 1.3. To change the brand tag, go to Step 1 of the topic settings.',
  brandTagMetricHeader: 'Metric',
  tierLabels: { new: 'New brand', mid: 'Mid-tier brand', leader: 'Industry leader' },
  industryClassLabels: { regular: 'Regular industry', special: 'Special industry (medical/beauty/finance)' },
  overviewBanner: (met, total) => `${met} of ${total} targeted metrics met (≥3 met = overall met)`,
  overviewBannerHint: 'Tier-tagged topics: target = lower bound of tier range; untagged: target = baseline × 1.3. Weekly basis',
  overviewTrendTitle: 'Overall exposure daily trend (last 30)',
  latestPrefix: 'Latest ',
  viewAllList: 'All',
  openReport: 'Open report',
  noTopicTitle: 'No monitored topics yet',
  noTopicHint: 'Topics are configured by the administrator; reports generate automatically once runs start.',

  loading: 'Loading…',
  empty: 'No data',
  view: 'View',
  exportPdf: 'Export PDF',
  exportClosing: 'Export closing report',
  exporting: 'Generating…',
  prevPeriod: 'Prev',
  nextPeriod: 'Next',
  backToCenter: 'Reports',

  metricLabels: {
    exposure: 'Overall exposure', top1: 'Top-1 exposure', top3: 'Top-3 exposure',
    accuracy: 'Content accuracy', citation: 'Citation rate', mention: 'Share of voice',
  },
  metricHints: {
    exposure: 'Coverage across query × engine pairs that mention the brand (incl. aliases).'
      + '\nOne pair = one monitored query asked on one engine; multiple rounds in the period collapse into one pair, counted as hit if any round hit.'
      + '\nNumerator = category pairs ever hit; denominator = category pairs covered this period (errors excluded).'
      + '\nWith a fixed denominator configured, the denominator is pinned to that value regardless of actual coverage; anything above it displays as 100%.',
    top1: 'Share of category pairs where the brand ranks #1, taking each pair\'s best round in the period.'
      + '\nWith a fixed denominator configured, this shares the same denominator as overall exposure.',
    top3: 'Share of category pairs where the brand ranks in the top 3 (incl. #1), taking each pair\'s best round.'
      + '\nWith a fixed denominator configured, this shares the same denominator as overall exposure.',
    accuracy: 'Among hit responses, share without negative/biased statements — counted per response, not deduplicated per pair.'
      + '\nHit responses falling inside a negative-mention verdict window count against accuracy; once re-checked and cleared they no longer count.',
    citation: 'Share of all query × engine pairs (brand queries included) that cited our own published content URLs.'
      + '\nMeasures whether AI trusts and cites our content supply; with a fixed denominator configured it is pinned to that value.',
    mention: 'Brand mentions as a share of all brand + competitor mentions in AI answers — counted per response, not deduplicated per pair.'
      + '\nNumerator: all hit responses by default; topics with the "category-only share of voice" switch count category-query hits only'
      + ' (a query naming the brand is always answered with the brand, so counting it inflates the metric). Denominator = brand hits + competitor mentions.'
      + '\nCompetitor mentions come from post-run extraction; when extraction is down the denominator is the brand alone and this metric pins at 100%.'
      + '\nTier-tagged topics get a tier-range target and count toward goal tracking; untagged topics have no target.',
  },
  hintTarget: 'Tier-tagged topics: target = the tier reference range; reaching the lower bound counts as met.\nUntagged topics: target = baseline × 1.3 (contract: +30%); baseline is the first data period, no target when baseline is 0.',
  hintDailyDetail: 'One row = one query × engine.\nRank is the best round today; N/M = hit rounds / sampled rounds (stability); change compares with yesterday, changed rows first.',
  hintBeforeAfter: 'Before = last ISO week of the previous month (first month falls back to the first ISO week overall); After = last ISO week of this month.\nRelative = (after - before) / before; ≥30% gets a Met check. 0 → positive counts as met (from zero).',
  zeroToSome: 'From zero',
  hintPlatformMatrix: 'Six metrics per engine for this month; darker = better within each column.',
  hintWeeklyPlatformMatrix: 'Six metrics per engine for this week; darker = better within each column.',
  hintTrendForecast: 'Solid line = weekly history; dashed = 4-week linear forecast at the current slope.',
  hintCorrections: 'Found → handled → re-checked log for negative mentions, plus unhit-query gaps.',
  hintWeeklyCorrections: 'Found → handled → re-checked log for negative mentions this week, plus unhit-query gaps.',
  hintPositionDist: 'One cell = one query × engine pair. Numbers inside the bars = how many cells of this query type fall in each best-ever rank bucket (Top1 / Top2-3 / #4-10 / missed).\nBrand and competitor-comparison queries name the brand in the question itself, so rank is meaningless there — they use three buckets: Top1 / Exposed / Missed.\nE.g. 6 brand queries × 5 engines = 30 cells; "21" in the Top1 segment means 21 cells reached rank 1.',
  hintCitationSources: 'Sources cited in AI answers. Left: type and owned share; right: top 10 platforms by citation count.\nType inferred from domain and title; Owned = our published content.',
  hintOverviewBanner: '≥3 targeted metrics met counts as overall met; weekly basis.\nTier-tagged topics: target = lower bound of the tier range; untagged: target = baseline × 1.3 (contract: +30%).',
  hintOverviewTrend: 'Daily overall exposure (last 30), showing the content → indexing → exposure lag.',
  basisChangeFlag: 'Basis change',
  basisChangeTitle: 'Basis changes',
  basisChangeHint: 'At the marked points the measurement basis changed (queries added or removed, '
    + 'engines added or paused, calculation rules adjusted). The denominators differ on either side, '
    + 'so the levels are not directly comparable — a rise or drop here may not reflect optimisation.',
  basisChangeKinds: {
    queries: 'Monitored queries changed',
    scope: 'Calculation basis adjusted',
    engines: 'Engine set changed',
    denom: 'Sample size shift',
  },
  basisChangeDesc: c => {
    if (c.kind === 'engines') {
      const parts: string[] = [];
      if (c.engines_added.length) parts.push(`added ${c.engines_added.map(engineLabel).join(', ')}`);
      if (c.engines_removed.length) parts.push(`paused ${c.engines_removed.map(engineLabel).join(', ')}`);
      return parts.join('; ');
    }
    if (c.before === null || c.after === null) {
      return c.after !== null ? `set to ${c.after}` : 'rules adjusted — earlier history uses another basis';
    }
    const pct = c.before > 0 ? Math.round(((c.after - c.before) / c.before) * 100) : null;
    const noun = c.kind === 'queries' ? 'Queries' : 'Valid answers';
    const delta = pct === null ? '' : ` (denominator ${pct >= 0 ? '+' : ''}${pct}%)`;
    return `${noun} ${c.before} → ${c.after}${delta}`;
  },
  trendNumDen: (num, den) => `${num} hits / ${den} pairs`,
  baseline: 'Baseline',
  target: 'Target',
  progressMet: 'Met',
  progressNear: 'Near',
  progressMissed: 'Missed',
  noTarget: '—',
  vsPrev: 'vs prev',
  firstPeriod: 'First-period baseline',

  centerTitle: 'Reports',
  tabDaily: 'Daily',
  tabWeekly: 'Weekly',
  tabMonthly: 'Monthly',
  colReport: 'Report',
  colKpiSummary: 'KPI summary',
  colResponses: 'Responses',
  colOps: 'Actions',
  dailyPrefix: 'Daily',
  weeklyPrefix: 'Weekly',
  monthlyPrefix: 'Monthly',
  emptyReports: 'No run data for this period yet',

  dailyTitle: 'Daily report',
  dailyScope: (w, e, r, n) => `${w} queries × ${e} engines × ${r} rounds · ${n} valid responses today`,
  secAiSummary: 'AI daily summary',
  aiSummaryHint: 'Auto-generated daily from yesterday\'s metric changes and hits; hidden until generated.',
  secKpiDiff: '① KPI day-over-day',
  secHighlights: '② Daily detail (query × engine)',
  colRound: 'Round',
  secActions: '③ Today\'s actions',
  secDetail: '④ Full detail',
  colMetric: 'Metric',
  colYesterday: 'Yesterday',
  colToday: 'Today',
  colDelta: 'DoD',
  colNote: 'Note',
  hlRounds: (n, m) => `${n}/${m} rounds hit`,
  hlYesterday: 'yesterday',
  hlNew: 'new',
  hlUp: 'up',
  hlDown: 'down',
  detailSummary: n => `${n} query × engine cells today`,
  noChangeToday: 'No rank changes vs yesterday',
  flatCollapsed: n => `${n} more unchanged cells — click to expand`,
  moreChanged: n => `show ${n} more changes`,
  colTodayRank: 'Today rank',
  colChange: 'Change',
  colActionTime: 'Time',
  colActionTitle: 'Action',
  emptyActions: 'No content actions today',
  colQuery: 'Query',
  colEngine: 'Engine',
  colHits: 'Hits / responses',
  hintHits: 'Category-query hits and responses for this engine this period (raw counts).\nHits ÷ responses = the overall exposure on the right; with a fixed denominator set, the denominator shown is the pinned one (configured value ÷ number of engines).',
  rankMissing: 'not collected',
  hintRankMissing: 'No rank was collected for this engine this period, so Top-1/Top-3 are not shown.\nRanks come from post-run analysis and go entirely blank when that analysis is down — which is not the same as "never ranked", so it is not shown as 0%.',
  colExposed: 'Exposed',
  colRank: 'Rank',
  colSentiment: 'Sentiment',
  colCitedOwn: 'Cites us',
  filterChangedOnly: 'Changed only',
  exposed: 'Exposed',
  notExposed: 'Missed',
  yes: 'Yes',
  no: 'No',
  sentimentLabels: { positive: 'Positive', neutral: 'Neutral', negative: 'Negative', unknown: '—' },
  detailCount: n => `${n} rows`,

  weeklyTitle: 'Weekly report',
  colBaseline: 'Baseline',
  colTarget: 'Target (+30%)',
  colProgress: 'Progress',
  emptyBody: 'No AI narrative generated for this week',

  monthlyTitle: 'Monthly report',
  verdictMet: 'KPI met',
  verdictPartial: 'KPI partially met',
  verdictMissed: 'KPI missed',
  secKpiCards: '① KPI overview (before → after)',
  hintWeeklyBeforeAfter: 'Before = baseline week (start of tracking); after = this week.\nRelative lift = (after - before) / before × 100%; ≥30% earns the met badge. Before = 0 with after > 0 counts as met (from nothing to something).',
  secPlatformMatrix: '② Engines × five metrics',
  secTrend: '③ Weekly exposure trend & forecast',
  secCorrections: '④ Corrections & gaps',
  secDeliverables: '⑤ Deliverables',
  secNextPlan: '⑥ Next phase',
  before: 'Before',
  after: 'After',
  relative: 'Relative',
  metBadge: 'Met',
  colIssueType: 'Issue',
  colFound: 'Found',
  colStatus: 'Status',
  colAction: 'Action',
  emptyCorrections: 'No corrections or unhit-query gaps this period',
  forecastLegend: 'Forecast (linear)',
  historyLegend: 'History',

  boardTitle: 'Metrics board',
  secPositionDist: 'Position distribution by query type',
  secCitationSources: 'Citation source analysis',
  sceneLabels: {
    compare: 'Comparison', brand: 'Brand', search: 'Search', qa: 'Q&A', intent: 'Intent',
    scenario: 'Scenario', unknown: 'Unclassified',
  },
  rankBadge: { top1: 'Top 1', top3: 'Top 3', r4_10: '#4-10', exposed: 'Exposed', miss: 'Missed', nodata: 'N/A' },
  distTop1: 'Top1',
  distTop23: 'Top2-3',
  dist410: '4-10',
  distMiss: 'Missed',
  distExposed: 'Exposed',
  colSourceTitle: 'Cited source',
  colSourceType: 'Type',
  colSourceEngines: 'Engines',
  colSourceCount: 'Citations',
  colSourceOwned: 'Owned',
  queryCount: n => `${n} queries`,
  totalCitations: n => `${n} citations total`,
  totalCitationsShort: 'Total citations',
  srcBarLegend: 'Top 10 platforms only',

  secCompetitive: 'Competitive analysis',
  secContrast: 'Competitive contrast',
  hintContrast: 'Industry average = mean of brand and all competitors; three metrics — coverage, mention share and rank — compare the brand, each competitor and the industry average (rank is inverted so higher = better position). The overview radar adds two more axes — content accuracy and citation rate: the brand values are real (same formula as the KPI cards), while competitor values cannot be observed directly and are estimated from the industry reference band of their exposure tier. Overview shows an overlaid radar; period reports use comparison bars. Latest ISO week, matching the KPI cards.',
  secInsight: 'Competitive intelligence',
  hintInsight: 'Coverage gap vs the leading competitor, plus the number of untapped opportunity keywords.',
  secHeat: 'Keyword heat map',
  hintHeat: '"Search heat" = real search demand for the keyword, pulled from external sources (Baidu Index, Google Trends, Ocean Engine); long-tail phrases no index covers are estimated by an LLM from the industry context, and every tile is labelled with its source. Tile area and colour depth both track the search heat. Search heat is cached and refreshed by a scheduled job; hit Refresh to re-collect now.',
  secRanking: 'Product ranking list',
  hintRanking: 'Exposure-rate scores (0-100) per AI engine: brand = overall exposure rate (same formula as the KPI card); competitor = share of answers it appears in. Composite = whole-window rate, identical to the contrast cards; engine columns are the per-engine breakdown.',
  kwCoverage: 'Overall exposure',
  hintKwCoverage: 'Brand = hit responses ÷ valid responses in the window (same formula as the KPI card); competitor = share of answers it appears in.',
  prodRank: 'Product rank',
  hintProdRank: 'Group rank from the product ranking list: brand and competitors sorted by the same composite exposure score, matching the list below; lower is better.',
  hintProdMention: 'Times the brand is mentioned as a share of all brand + competitor mentions in the same batch of AI answers.\nNumerator = brand mentions; denominator = brand + competitor mentions combined.\nHigher = a larger share of the conversation belongs to us.',
  prodMentionShort: 'Mention share',
  rankUnit: '',
  rankPrefix: '#',
  cmpOurs: 'Ours',
  cmpTop: 'Top',
  cmpIndustry: 'Ind.',
  vsPrevShort: 'vs prev',
  industryAvg: 'Industry average',
  gapTop2: 'Exposure gap vs top competitor',
  hintGapTop2: 'Our overall exposure minus the top competitor appearance rate; positive = leading.',
  targetProgress: 'Target progress',
  vsIndustryPp: diff => diff >= 0
    ? `${diff}pp above industry average`
    : `${Math.abs(diff)}pp below industry average`,
  vsIndustryRank: diff => diff > 0
    ? `${diff} place(s) ahead of industry average`
    : diff < 0 ? `${Math.abs(diff)} place(s) behind industry average` : 'On par with industry average',
  improveBtn: 'Improve',
  optimizeKw: 'Keywords to optimize',
  hintOptimizeKw: 'Monitored keywords with zero hits in the window — opportunity keywords.',
  getBtn: 'View',
  actionRailTitle: 'Action items',
  heatLow: 'Low',
  heatHigh: 'High',
  heatWeak: 'Weak',
  heatStrong: 'Strong',
  heatRankCol: 'Rank',
  heatKwCol: 'Keyword',
  heatValCol: 'Heat',
  heatSourceLabels: {
    baidu_index: 'Baidu Index',
    google_trends: 'Google Trends',
    oceanengine: 'Ocean Engine',
    llm: 'LLM estimate',
  },
  heatCoreWord: 'Queried as',
  heatNoSearch: 'Not collected',
  heatStaleTag: 'Stale',
  heatRefresh: 'Refresh',
  heatRefreshing: 'Collecting…',
  heatCollected: (fresh, total) => `Search heat: ${fresh}/${total} collected`,
  heatRefreshDone: written => `Search heat updated for ${written} keywords`,
  heatRefreshSkipped: sources => `Sources that returned nothing:\n${sources}`,
  heatRefreshFailed: 'Search-heat collection failed, please retry later',
  heatTooltip: search => `Search heat ${search}`,
  rankingRankCol: 'Rank',
  rankingProductCol: 'Product/Brand',
  compositeCol: 'Composite',
  hintComposite: 'Whole-window exposure/appearance rate — the same number as the contrast cards and KPI cards.',
  actionCol: 'Action',
  boostBtn: 'Boost rank',
  brandSelf: 'Ours',
  monitorTarget: 'Monitored brand',
  relatedComp: 'Competitor',
  rankHeatHint: 'Cell shade = per-engine exposure score',
  viewFullCompetitive: 'Full competitive analysis',
  cmpVerdict: (name, lead, total) => `vs "${name}" this period: leading ${lead} of ${total} core metrics`,
  leadBadge: 'Lead',
  trailBadge: 'Trail',
  rankValue: n => (n > 0 ? `#${n}` : '—'),

  searchPlaceholder: 'Search query…',
  pagePrev: 'Prev',
  pageNext: 'Next',
};

export function useReportsLang(): Dict {
  // useTranslation subscribes to languageChanged -- switching language re-renders immediately, no refresh needed
  const { i18n } = useTranslation();
  const lng = (i18n.language || 'zh').toLowerCase();
  return lng.startsWith('en') ? EN : ZH;
}
