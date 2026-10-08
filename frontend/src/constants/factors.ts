// label / color / desc / ordering of source GEO factors (backend source_factor.classify_factor, FirstPageSage metric definition)
// -- the **single authoritative source**.
//
// Used to live in pages/BrandGrowth/lang.ts. After the brand growth module was retired these mappings are still used by the report center
// (BoardSections) and the industry library (IndustryDetail), so they were moved to constants/ permanently,
// not deleted with the page components. Keys are aligned with backend source_factor.classify_factor.

import i18n from '../i18n';

// ── Source GEO factors (backend classify_factor, FirstPageSage metric definition) label / color / ordering ──
// Keys aligned with backend source_factor.classify_factor
export const FACTOR_LABELS_ZH: Record<string, string> = {
  list_mention: '权威榜单提及',
  reviews: '在线评价',
  directory: '数据库与目录',
  awards: '奖项资质',
  customer: '客户案例与使用数据',
  social: '社交口碑',
  authority: '权威机构',
  tech_community: '技术社区',
  news_media: '媒体报道',
  vendor: '厂商与官方资料',
  other: '其它',
};

export const FACTOR_LABELS_EN: Record<string, string> = {
  list_mention: 'Authoritative List Mentions',
  reviews: 'Online Reviews',
  directory: 'Databases & Directories',
  awards: 'Awards & Accreditations',
  customer: 'Customer Examples & Usage Data',
  social: 'Social Sentiment',
  authority: 'Authoritative Institutions',
  tech_community: 'Tech Communities',
  news_media: 'Media Coverage',
  vendor: 'Vendor & Official Resources',
  other: 'Other',
};

export function factorLabel(factor: string): string {
  const lng = (i18n.language || 'zh').toLowerCase();
  const map = lng.startsWith('en') ? FACTOR_LABELS_EN : FACTOR_LABELS_ZH;
  return map[factor] || map.other;
}

// The overall donut / per-engine donuts share one set of colors, so the same factor always has the same color
export const FACTOR_COLORS: Record<string, string> = {
  list_mention: '#2563eb',
  reviews: '#f59e0b',
  directory: '#7c6f9e',
  awards: '#ef4444',
  customer: '#10b981',
  social: '#06b6d4',
  authority: '#4f46e5',
  tech_community: '#8b5cf6',
  news_media: '#ec4899',
  vendor: '#0ea5e9',
  other: '#64748b',
};

export const FACTOR_ORDER: string[] = [
  'list_mention', 'reviews', 'directory', 'awards', 'customer', 'social',
  'tech_community', 'news_media', 'vendor', 'authority', 'other',
];

export function factorColor(factor: string): string {
  return FACTOR_COLORS[factor] || FACTOR_COLORS.other;
}

// Plain-language explanation of each factor type -- shown when hovering the ? next to a factor name in the legend
export const FACTOR_DESC_ZH: Record<string, string> = {
  list_mention: '权威榜单提及:被第三方榜单、排行、盘点类文章点名,如「十大」「推荐榜」「年度精选」。这类信源最容易被 AI 当作客观推荐引用。',
  reviews: '在线评价:来自点评、评分类平台的用户口碑与打分,反映真实使用反馈。',
  directory: '数据库与目录:被收录进行业目录、企业名录、产品库等结构化数据站点,便于 AI 检索核对基本信息。',
  awards: '奖项资质:获奖、认证、资质、官方背书类内容,是 AI 判断可信度的重要凭据。',
  customer: '客户案例与使用数据:客户案例、成功故事、用量 / 业绩数据等能证明实际效果的内容。',
  social: '社交口碑:社交媒体、社区、论坛上的讨论与提及,反映大众关注度与话题热度。',
  authority: '权威机构:政务、学术院所、行业协会等机构站点,以及品牌官网页面,域名权重与公信力高。',
  tech_community: '技术社区:开发者社区、技术博客、云厂商开发者专栏等专业内容,常被 AI 引作技术依据。',
  news_media: '媒体报道:新闻门户、行业媒体、自媒体资讯对品牌或品类的报道与解读。',
  vendor: '厂商与官方资料:厂商官网的产品页、解决方案、官方文档等一手资料。',
  other: '其它:未能归入以上任一类的信源。',
};

export const FACTOR_DESC_EN: Record<string, string> = {
  list_mention: 'Authoritative list mentions: named in third-party rankings, "best-of" lists or roundups — the kind of source AI treats as an objective recommendation.',
  reviews: 'Online reviews: ratings and user feedback from review platforms, reflecting real-world experience.',
  directory: 'Databases & directories: listed in industry directories, business registries or product databases — structured data AI can cross-check.',
  awards: 'Awards & accreditations: awards, certifications and official endorsements that AI uses to judge credibility.',
  customer: 'Customer examples & usage data: case studies, success stories and usage / performance figures that prove real results.',
  social: 'Social sentiment: discussion and mentions on social media, communities and forums — a signal of public attention.',
  authority: 'Authoritative institutions: government, academic and industry-association sites plus the brand\'s own pages — high domain weight and credibility.',
  tech_community: 'Tech communities: developer communities, technical blogs and cloud-vendor developer columns that AI cites as technical evidence.',
  news_media: 'Media coverage: news portals, industry media and independent outlets reporting on the brand or category.',
  vendor: 'Vendor & official resources: first-party product pages, solution briefs and official documentation from vendors.',
  other: 'Other: sources that do not fall into any of the categories above.',
};

export function factorDesc(factor: string): string {
  const lng = (i18n.language || 'zh').toLowerCase();
  const map = lng.startsWith('en') ? FACTOR_DESC_EN : FACTOR_DESC_ZH;
  return map[factor] || map.other;
}
