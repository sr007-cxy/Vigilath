// Second-pass parsers that turn `check.message` strings (emitted by
// geo_checker.py) into structured data for the visual cards.
//
// Each parser is defensive: when the expected pattern isn't found, it returns
// `null` so the CategoryVisual dispatcher can fall back to the plain row list.
// Do NOT throw from these — rendering must never crash on an unexpected msg.

import type { CheckResult } from '../../../types/geo';

// ---- Shared helpers --------------------------------------------------------

const byStatus = (checks: CheckResult[], pred: (msg: string) => boolean, status?: string) =>
  checks.find((c) => (!status || c.status === status) && pred(c.message));

// ---- robots.txt: AI crawler rule summary -----------------------------------
//
// Source messages (see geo_checker/checks.py:check_robots_txt):
//   [PASS] robots.txt found (N bytes)
//   [FAIL] robots.txt not found at URL
//   [PASS] robots.txt references a sitemap
//   [WARN] robots.txt does not reference a sitemap
//   [WARN] Wildcard user-agent blocks all crawlers (Disallow: /)
//   [WARN] AI crawler rules: N user-agent(s) explicitly blocked
//   [PASS] AI crawler rules: N user-agent(s) explicitly allowed
//   [INFO] AI crawler rules: N user-agent(s) inherit the wildcard rule
//
// The backend deliberately no longer emits crawler user-agent names — the card
// shows counts only. Records written before that change carry the old
// "AI bots explicitly BLOCKED: <names>" form; we count those names without ever
// surfacing them, so historical results keep rendering the right numbers.

export interface RobotsParsed {
  fileFound: boolean;
  hasSitemapRef: boolean | null;
  wildcardBlocksAll: boolean;
  blockedCount: number;
  allowedCount: number;
  inheritedCount: number;
}

/** Count from the current "…: N user-agent(s) …" form, else from a legacy name list. */
const botCount = (message: string | undefined, legacyMarker: string): number => {
  if (!message) return 0;
  const m = /:\s*(\d+)\s+user-agent/.exec(message);
  if (m) return Number(m[1]);
  const idx = message.indexOf(legacyMarker);
  if (idx < 0) return 0;
  return message
    .slice(idx + legacyMarker.length)
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean).length;
};

export function parseRobotsTxt(checks: CheckResult[]): RobotsParsed | null {
  if (!checks || checks.length === 0) return null;

  const fileFound = !!byStatus(checks, (m) => m.startsWith('robots.txt found'));
  const fileMissing = !!byStatus(checks, (m) => m.startsWith('robots.txt not found'));
  if (!fileFound && !fileMissing) return null;

  const hasSitemapRef = byStatus(checks, (m) => m.startsWith('robots.txt references a sitemap'))
    ? true
    : byStatus(checks, (m) => m.startsWith('robots.txt does not reference a sitemap'))
    ? false
    : null;

  const wildcardBlocksAll = !!byStatus(checks, (m) =>
    m.startsWith('Wildcard user-agent blocks all crawlers'),
  );

  const blockedMsg = byStatus(
    checks,
    (m) => m.includes('explicitly blocked') || m.startsWith('AI bots explicitly BLOCKED:'),
  );
  const allowedMsg = byStatus(
    checks,
    (m) => m.includes('explicitly allowed') || m.startsWith('AI bots with directives (not blocked):'),
  );
  const inheritedMsg = byStatus(
    checks,
    (m) => m.includes('inherit the wildcard rule') || m.startsWith('AI bots not mentioned'),
  );

  return {
    fileFound,
    hasSitemapRef,
    wildcardBlocksAll,
    blockedCount: botCount(blockedMsg?.message, 'BLOCKED:'),
    allowedCount: botCount(allowedMsg?.message, '(not blocked):'),
    inheritedCount: botCount(inheritedMsg?.message, 'inherit wildcard rules):'),
  };
}

// ---- Meta Tags: 6 sub-item grid -------------------------------------------
//
// Source messages (see geo_checker.py:check_meta_tags):
//   [PASS] <title> found: "..."
//   [FAIL] Missing <title> tag
//   [PASS] Meta description found (N chars)
//   [FAIL] Missing meta description
//   [WARN] Meta description is very short — ...
//   [PASS] Canonical URL set: URL
//   [WARN] No canonical URL — ...
//   [PASS] Open Graph tags found: og:title, og:image
//   [WARN] No Open Graph tags — ...
//   [PASS] Language declared: en
//   [WARN] No lang attribute on <html> — ...
//   [PASS] Hreflang tags found for: en, es
//   [INFO] No hreflang tags — ...

export type MetaItemStatus = 'PASS' | 'WARN' | 'FAIL' | 'INFO' | 'UNKNOWN';

export interface MetaItem {
  key: 'title' | 'description' | 'canonical' | 'og' | 'lang' | 'hreflang';
  status: MetaItemStatus;
  detail?: string;
}

export interface MetaParsed {
  items: MetaItem[];
}

const classify = (
  checks: CheckResult[],
  detect: {
    passPrefix?: string;
    failPrefix?: string;
    warnPrefix?: string;
    infoPrefix?: string;
  },
): { status: MetaItemStatus; detail?: string } => {
  const match = (prefix: string | undefined, s: string) => {
    if (!prefix) return undefined;
    return checks.find((c) => c.status === s && c.message.startsWith(prefix));
  };
  const pass = match(detect.passPrefix, 'PASS');
  if (pass) return { status: 'PASS', detail: pass.message.slice(detect.passPrefix!.length).trim() };
  const fail = match(detect.failPrefix, 'FAIL');
  if (fail) return { status: 'FAIL', detail: fail.message };
  const warn = match(detect.warnPrefix, 'WARN');
  if (warn) return { status: 'WARN', detail: warn.message };
  const info = match(detect.infoPrefix, 'INFO');
  if (info) return { status: 'INFO', detail: info.message };
  return { status: 'UNKNOWN' };
};

export function parseMetaTags(checks: CheckResult[]): MetaParsed | null {
  if (!checks || checks.length === 0) return null;

  const items: MetaItem[] = [
    {
      key: 'title',
      ...classify(checks, { passPrefix: '<title> found:', failPrefix: 'Missing <title>' }),
    },
    {
      key: 'description',
      ...classify(checks, {
        passPrefix: 'Meta description found',
        failPrefix: 'Missing meta description',
        warnPrefix: 'Meta description is very short',
      }),
    },
    {
      key: 'canonical',
      ...classify(checks, { passPrefix: 'Canonical URL set:', warnPrefix: 'No canonical URL' }),
    },
    {
      key: 'og',
      ...classify(checks, { passPrefix: 'Open Graph tags found:', warnPrefix: 'No Open Graph tags' }),
    },
    {
      key: 'lang',
      ...classify(checks, { passPrefix: 'Language declared:', warnPrefix: 'No lang attribute' }),
    },
    {
      key: 'hreflang',
      ...classify(checks, { passPrefix: 'Hreflang tags found for:', infoPrefix: 'No hreflang tags' }),
    },
  ];

  // If every single item is UNKNOWN, give up and fall back to list view.
  if (items.every((i) => i.status === 'UNKNOWN')) return null;
  return { items };
}

// ---- Cross-Platform / Social Signals: platform grid -----------------------
//
// Source messages (see geo_checker.py:check_cross_platform):
//   [PASS] X / Twitter       linked on site: https://...
//   [PASS] LinkedIn          profile found: https://...
//   [INFO] GitHub            not detected
//   [PASS|WARN|FAIL] Strong/Moderate/Limited cross-platform presence: N/10 platforms
//
// The platform name is left-padded to 16 chars; we split on the status keywords.

// Must stay in sync with `platforms` in geo_checker/checks.py:check_cross_platform.
export const KNOWN_PLATFORMS = [
  '微信公众号',
  '微博',
  '知乎',
  '小红书',
  '抖音',
  '哔哩哔哩',
  '百家号',
  '今日头条',
  'CSDN',
  'Gitee',
] as const;

export type PlatformState = 'linked' | 'probed' | 'notFound' | 'unknown';

export interface PlatformEntry {
  name: string;
  state: PlatformState;
  url?: string;
}

export interface PlatformParsed {
  platforms: PlatformEntry[];
  foundCount: number;
  totalCount: number;
}

// One regex per phrase — the middle group is the trailing URL / empty.
const LINKED_RE = /^(.+?)\s+linked on site:\s*(\S+)/;
const PROBED_RE = /^(.+?)\s+profile found:\s*(\S+)/;
const NOT_FOUND_RE = /^(.+?)\s+not detected/;
const SUMMARY_RE = /(?:presence|presence:)\s*(\d+)\s*\/\s*(\d+)\s*platforms/i;

export function parseCrossPlatform(checks: CheckResult[]): PlatformParsed | null {
  if (!checks || checks.length === 0) return null;

  const map = new Map<string, PlatformEntry>();

  const normalizeName = (raw: string): string => {
    const trimmed = raw.trim();
    // Find a known platform whose name starts the candidate
    const match = KNOWN_PLATFORMS.find(
      (p) => trimmed === p || trimmed.toLowerCase() === p.toLowerCase(),
    );
    return match ?? trimmed;
  };

  for (const c of checks) {
    let m = LINKED_RE.exec(c.message);
    if (m) {
      const name = normalizeName(m[1]);
      map.set(name, { name, state: 'linked', url: m[2] });
      continue;
    }
    m = PROBED_RE.exec(c.message);
    if (m) {
      const name = normalizeName(m[1]);
      map.set(name, { name, state: 'probed', url: m[2] });
      continue;
    }
    m = NOT_FOUND_RE.exec(c.message);
    if (m) {
      const name = normalizeName(m[1]);
      if (!map.has(name)) map.set(name, { name, state: 'notFound' });
      continue;
    }
  }

  if (map.size === 0) return null;

  // Always return the full known list, filling unknowns as 'unknown'
  const platforms: PlatformEntry[] = KNOWN_PLATFORMS.map(
    (p) => map.get(p) ?? { name: p, state: 'unknown' as PlatformState },
  );

  // Pull counts from summary line if present; otherwise derive.
  let foundCount = platforms.filter((p) => p.state === 'linked' || p.state === 'probed').length;
  let totalCount = platforms.length;
  const summary = checks.find((c) => SUMMARY_RE.test(c.message));
  if (summary) {
    const m = SUMMARY_RE.exec(summary.message);
    if (m) {
      foundCount = Number(m[1]);
      totalCount = Number(m[2]);
    }
  }

  return { platforms, foundCount, totalCount };
}
