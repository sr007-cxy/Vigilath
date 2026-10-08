// Industry library (public page) copy — metric / section names match the report center dictionary (pages/Reports/lang.ts) verbatim,
// no invented names; page-level copy (hero / card descriptions / CTA) is new in this module.
import { useTranslation } from 'react-i18next';

export interface IndustryLang {
  // hero / list page
  libTitle: string;
  libSubtitle: string;
  libCardBrands: (n: number) => string;
  libCardKeywords: (n: number) => string;
  libCardEngines: (n: number) => string;
  libCardUpdated: (d: string) => string;
  libCardTopBrands: string;
  libCardView: string;
  libEmpty: string;
  detailTitlePrefix: string;              // Suffix concatenated after "{industry name}"
  searchPlaceholder: string;
  backToLib: string;
  updatedAt: (d: string) => string;
  // Three industry overview cards
  secVisibility: string;
  heatCard: string;
  heatCardSub: string;
  top1Card: string;
  top1CardSub: (cov: number) => string;
  cr3Card: string;
  cr3CardSub: string;
  // Leading brand comparison / insights
  secContrast: string;
  secInsight: string;
  industryAvg: string;
  kwCoverage: string;
  prodRank: string;
  prodMention: string;
  vsAvgAbove: (v: number) => string;
  vsAvgBelow: (v: number) => string;
  top1Lead: string;
  top1LeadSub: (a: string, b: string) => string;
  optimizeKw: string;
  optimizeKwSub: string;
  improveBtn: string;
  getBtn: string;
  // Citation source analysis
  secSources: string;
  sourceTypeShare: string;
  topSources: string;
  sourceCountCol: string;
  sourceEnginesCol: string;
  totalCitations: (n: number) => string;
  // Heat map
  secHeat: string;
  heatRankCol: string;
  heatWordCol: string;
  heatValueCol: string;
  cloudMonitorLegend: string;
  cloudSuggestLegend: string;
  // Leaderboard
  secRanking: string;
  rankCol: string;
  rankingProductCol: string;
  compositeCol: string;
  actionCol: string;
  improveRankBtn: string;
  relatedBrand: string;
  rankValue: (n: number) => string;
  loading: string;
  loadFailed: string;
}

const ZH: IndustryLang = {
  libTitle: 'AI 搜索行业数据库',
  libSubtitle: '基于真实 AI 引擎应答的行业可见性数据:各品牌在 DeepSeek、豆包、通义千问等引擎中的露出、排名与声量,持续监测、每日更新。',
  libCardBrands: n => `${n} 个品牌`,
  libCardKeywords: n => `${n} 个提示词`,
  libCardEngines: n => `${n} 个 AI 引擎`,
  libCardUpdated: d => `数据截至 ${d}`,
  libCardTopBrands: '头部品牌',
  libCardView: '查看行业数据',
  libEmpty: '行业数据整理中,敬请期待',
  detailTitlePrefix: '行业 AI 可见性数据',
  searchPlaceholder: '搜索品牌,查看它在榜单中的位置',
  backToLib: '返回行业库',
  updatedAt: d => `数据截至 ${d} · 来源:AI 引擎真实应答持续监测`,
  secVisibility: '行业概览',
  heatCard: '行业声量',
  heatCardSub: 'AI 应答中的品牌提及总次数',
  top1Card: 'TOP1 品牌',
  top1CardSub: cov => `当前行业第一,整体露出率 ${cov}%`,
  cr3Card: '品牌集中度',
  cr3CardSub: '头部三品牌占行业总声量比例',
  secContrast: '头部品牌对比',
  secInsight: '市场竞争洞察',
  industryAvg: '行业平均',
  kwCoverage: '整体露出率',
  prodRank: '产品排名',
  prodMention: '品牌声量度',
  vsAvgAbove: v => `高于行业平均 ${v}%`,
  vsAvgBelow: v => `低于行业平均 ${v}%`,
  top1Lead: 'TOP1 领先优势',
  top1LeadSub: (a, b) => `${a} 对 ${b} 的露出率领先幅度`,
  optimizeKw: '机会关键词',
  optimizeKwSub: 'AI 尚未形成稳定品牌推荐的提示词',
  improveBtn: '提 升',
  getBtn: '获 取',
  secSources: '引用源分析',
  sourceTypeShare: '信源类型占比',
  topSources: 'TOP 引用信源',
  sourceCountCol: '引用次数',
  sourceEnginesCol: '覆盖引擎',
  totalCitations: n => `共 ${n.toLocaleString()} 次引用`,
  secHeat: '关键词热量图',
  heatRankCol: '排名',
  heatWordCol: '关键词名称',
  heatValueCol: '热度值',
  cloudMonitorLegend: '行业提示词',
  cloudSuggestLegend: '相关搜索词(百度)',
  secRanking: '品牌排行榜',
  rankCol: '排名',
  rankingProductCol: '产品/品牌',
  compositeCol: '综合分数',
  actionCol: '操作',
  improveRankBtn: '提升排名',
  relatedBrand: '相关品牌',
  rankValue: n => (n > 0 ? `Top ${n}` : '—'),
  loading: '行业数据加载中…',
  loadFailed: '行业数据加载失败,请稍后重试',
};

const EN: IndustryLang = {
  libTitle: 'AI Search Industry Database',
  libSubtitle: 'Industry visibility data from real AI-engine answers: brand exposure, rank and share of voice across DeepSeek, Doubao, Qwen and more — continuously monitored, updated daily.',
  libCardBrands: n => `${n} brands`,
  libCardKeywords: n => `${n} monitored keywords`,
  libCardEngines: n => `${n} AI engines`,
  libCardUpdated: d => `Data as of ${d}`,
  libCardTopBrands: 'Top brands',
  libCardView: 'View industry data',
  libEmpty: 'Industry data coming soon',
  detailTitlePrefix: 'Industry AI Visibility Data',
  searchPlaceholder: 'Search a brand to locate it in the ranking',
  backToLib: 'Back to industries',
  updatedAt: d => `Data as of ${d} · Source: continuous monitoring of real AI-engine answers`,
  secVisibility: 'Industry Overview',
  heatCard: 'Industry voice',
  heatCardSub: 'Total brand mentions across AI answers',
  top1Card: 'Top brand',
  top1CardSub: cov => `Current industry leader, overall exposure ${cov}%`,
  cr3Card: 'Brand concentration',
  cr3CardSub: 'Top-3 brands share of total industry voice (CR3)',
  secContrast: 'Top Brands Contrast',
  secInsight: 'Competitive Intelligence',
  industryAvg: 'Industry average',
  kwCoverage: 'Overall exposure',
  prodRank: 'Product rank',
  prodMention: 'Mention share',
  vsAvgAbove: v => `${v}% above industry average`,
  vsAvgBelow: v => `${v}% below industry average`,
  top1Lead: 'Top-1 lead',
  top1LeadSub: (a, b) => `${a}'s exposure lead over ${b}`,
  optimizeKw: 'Opportunity keywords',
  optimizeKwSub: 'Keywords where AI has no stable brand recommendation yet',
  improveBtn: 'Improve',
  getBtn: 'View',
  secSources: 'Citation Sources',
  sourceTypeShare: 'Source type share',
  topSources: 'Top cited sources',
  sourceCountCol: 'Citations',
  sourceEnginesCol: 'Engines',
  totalCitations: n => `${n.toLocaleString()} citations total`,
  secHeat: 'Keyword heat map',
  heatRankCol: '#',
  heatWordCol: 'Keyword',
  heatValueCol: 'Heat',
  cloudMonitorLegend: 'Monitored keywords',
  cloudSuggestLegend: 'Related searches (Baidu)',
  secRanking: 'Brand Ranking List',
  rankCol: '#',
  rankingProductCol: 'Product/Brand',
  compositeCol: 'Composite',
  actionCol: 'Action',
  improveRankBtn: 'Improve rank',
  relatedBrand: 'Related brand',
  rankValue: n => (n > 0 ? `Top ${n}` : '—'),
  loading: 'Loading industry data…',
  loadFailed: 'Failed to load industry data, please retry later',
};

export function useIndustryLang(): IndustryLang {
  const { i18n } = useTranslation();
  return (i18n.language || 'zh').toLowerCase().startsWith('en') ? EN : ZH;
}
