/**
 * SEO category grouping for the result page's SEO dimension.
 *
 * Same contract as `categoryGroups` in pages/Result.tsx: the strings below MUST
 * match the `--- SEO: X ---` section headers printed by
 * backend/geo_checker/seo_checks.py, because the backend parser fills
 * `checks[].category` straight from those headers. A typo here silently drops a
 * whole category into the "other" bucket.
 *
 * Registry of the 12 labels: backend/geo_checker/orchestrate.py SEO_CHECK_REGISTRY.
 * Point totals per group are documented for review; they are not used at
 * runtime (the UI derives pass rates from the returned checks).
 */
export const seoCategoryGroups: Record<string, string[]> = {
  // 🆓 Page basics -- 22 points
  seoPageBasics: [
    'SEO: Title Tag',                    // 8
    'SEO: Meta Description',             // 6
    'SEO: Heading Structure',            // 8
  ],
  // 💎 Indexing & canonicalization -- 28 points
  seoIndexing: [
    'SEO: Indexability',                 // 10
    'SEO: URL, Canonical & Redirects',   // 10
    'SEO: Crawl Directives & Sitemap',   // 8
  ],
  // 💎 Links & images -- 15 points
  seoLinksMedia: [
    'SEO: Internal Linking',             // 8
    'SEO: Image SEO',                    // 7
  ],
  // 💎 Rich media & internationalization -- 10 points
  seoRichIntl: [
    'SEO: Rich Result Schema',           // 6
    'SEO: International Targeting',      // 4
  ],
  // 💎 Speed & mobile -- 13 points
  seoSpeedMobile: [
    'SEO: Page Speed Signals',           // 8
    'SEO: Mobile Usability',             // 5
  ],
};

/**
 * Groups every visitor can see in full. Mirrors FREE_GROUPS on the GEO side:
 * free and paid groups never overlap, so a whole paid group can render as 🔒
 * without having to lock individual categories inside a mixed group.
 *
 * Must stay in sync with FREE_SEO_CATEGORIES in
 * backend/geo_checker/orchestrate.py — the backend is the authority on what is
 * actually returned; this set only decides which tab shows a lock.
 */
export const FREE_SEO_GROUPS = new Set(['seoPageBasics']);

/** Default tab when the user switches to the SEO dimension. */
export const DEFAULT_SEO_TAB = 'seoPageBasics';
