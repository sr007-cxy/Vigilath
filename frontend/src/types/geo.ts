export interface CheckResult {
  category: string;
  status: string; // PASS, WARN, FAIL, INFO
  message: string;
  fix?: string;
  /** i18n key path under result.checks.* when the check was emitted
   *  via geo_checker.emit_check(). Absent for un-migrated legacy checks. */
  message_key?: string | null;
  /** Interpolation params for message_key (e.g. {count: 12, url: "..."}). */
  message_params?: Record<string, unknown> | null;
  /** i18n key under result.fixes.* for the fix recommendation. Absent for
   *  legacy fix() calls that haven't been migrated to emit_fix(). */
  fix_key?: string | null;
  /** Interpolation params for fix_key. */
  fix_params?: Record<string, unknown> | null;
}

export interface GeoTestRequest {
  url: string;
  include_fix: boolean;
  /** Also run the 12-category SEO suite and return it under `seo`. One
   *  request stays one billable check — the backend charges quota once and
   *  files a single history row with the SEO block in the same snapshot. */
  include_seo?: boolean;
}

/**
 * Classic SEO audit result. Same shape as GeoTestResult on purpose: the SEO
 * suite emits the identical line protocol, so `CheckResult` and the whole
 * grouping / paywall / i18n machinery are reused as-is. The two reports stay
 * distinguishable by the `SEO: ` category prefix and by living in separate
 * score namespaces — the scores are never averaged together.
 */
export interface SeoTestResult {
  url: string;
  score: number;
  grade: string;
  checks: CheckResult[];
  summary: {
    pass_count: number;
    warn_count: number;
    fail_count: number;
    info_count: number;
    total_checks: number;
  };
  tier?: string | null;
  locked_categories?: string[] | null;
}

export interface GeoTestResult {
  url: string;
  score: number;
  grade: string;
  checks: CheckResult[];
  summary: {
    pass_count: number;
    warn_count: number;
    fail_count: number;
    info_count: number;
    total_checks: number;
  };
  /** Membership slug under which this check ran ('free' | 'pro' | 'starter' | 'growth' | 'scale'). */
  tier?: string | null;
  /** Category labels that were NOT executed for this tier. Empty for unlocked tiers. */
  locked_categories?: string[] | null;
  /** Present when the request asked for `include_seo`. Null when the SEO half
   *  failed — a broken SEO suite never takes the GEO result down with it, so
   *  the result page renders a retry on the SEO side and keeps GEO intact. */
  seo?: SeoTestResult | null;
  /** History row id recorded by this audit, only for logged-in users. The result page uses it to export a vector PDF via backend Chrome printing;
   *  when empty (anonymous) it falls back to the frontend html2canvas export. */
  record_id?: number | null;
}
