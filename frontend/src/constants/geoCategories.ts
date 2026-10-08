/**
 * Grouping table for GEO (AI visibility) audits.
 *
 * Extracted from pages/Result.tsx: the result page and the PDF print page (pages/ResultPrint.tsx)
 * must use the same grouping, or the same audit would land in different tabs on the web and in the PDF.
 * Same contract as constants/seoCategories.ts.
 *
 * The 25 categories are split into 7 tabs (2 free + 5 paid), aligned with
 * docs/02-需求分析/计费与支付/会员功能免费与付费功能项目列表.md §检测大项分组. Free and paid groups don't overlap,
 * so an entire paid tab can show 🔒 to non-members, while free tabs are always visible.
 *
 * The strings here **must** match the `--- X ---` section headings printed by geo_checker character for character --
 * the backend parser uses those headings directly to fill `checks[].category`, and a typo makes the whole category fall into
 * the "Other" bucket, without any error.
 */
export const geoCategoryGroups: Record<string, string[]> = {
  // 🆓 Basic protocols & crawlability
  infraProtocols: [
    'HTTPS',
    'robots.txt',
    'sitemap.xml',
  ],
  // 🆓 Page basics & mobile experience
  pageBasics: [
    'Meta Tags',
    'Mobile-Friendliness & Page Weight',
  ],
  // 💎 AI-specific protocols & crawling
  aiProtocols: [
    'llms.txt',
    '.well-known Discovery',
    'AI Crawl Readiness',
  ],
  // 💎 Structure & semantics
  structuredSemantic: [
    'Structured Data',
    'Schema Breadcrumbs & Knowledge Panel',
    'URL Normalization',
  ],
  // 💎 Content quality & readability
  contentQuality: [
    'Content Accessibility',
    'Content Quality for AI',
    'AI-Specific Optimization',
    'AI Answer Format Optimization',
  ],
  // 💎 Technical robustness & media
  techRobustness: [
    'Technical Crawlability',
    'Outbound Links & Media',
    'Multilingual Content Depth',
    'Multi-Page Sampling',
  ],
  // 💎 Authority & external signals
  authorityExternal: [
    'Search Engine & AI Platform Registration',
    'Authority & Trust Signals',
    'Brand Entity in Knowledge Graphs',
    'Trust & Safety Signals',
    'Social Signals',
    'Cross-Platform Content Distribution',
  ],
};

/**
 * These two tabs render for everyone; the rest are paid tabs, and non-members see 🔒
 * (the criterion is that a category under the tab is in `locked_categories` sent by the backend).
 */
export const FREE_GEO_GROUPS = new Set(['infraProtocols', 'pageBasics']);
