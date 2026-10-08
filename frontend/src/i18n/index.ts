import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

// ICP filing remediation (D3-a): the site is Chinese-only, and the English resource bundle has been deleted from the repo.
// The language type is kept as a single-member union, for easy extension if needed later.
type Lng = 'zh';
type Ns = 'main' | 'result' | 'knowledge';

// Tracks which (lng, ns) packs have been addResourceBundle'd, keyed as
// "lng:ns" so one user's session never re-fetches the same chunk.
const loaded = new Set<string>();

async function loadPack(ns: Ns, lng: Lng): Promise<void> {
  const key = `${lng}:${ns}`;
  if (loaded.has(key)) return;
  let m: { default: Record<string, unknown> };
  if (ns === 'main') {
    m = await import('./zh');
  } else if (ns === 'result') {
    m = await import('./zh-result');
  } else {
    m = await import('./zh-knowledge');
  }
  i18n.addResourceBundle(lng, 'translation', m.default, true, true);
  loaded.add(key);
}

/**
 * Bootstrap hook called from main.tsx BEFORE createRoot.render. Awaits the
 * initial language's main pack so the first React render never shows raw
 * translation keys. Safe to call twice — re-entry is a no-op.
 */
export async function initI18n(): Promise<void> {
  if (i18n.isInitialized) return;
  // Kick off the chunk fetch in parallel with i18n.init so the two I/O waits
  // overlap instead of running back-to-back.
  const packPromise = loadPack('main', 'zh');
  await i18n.use(initReactI18next).init({
    resources: {},
    lng: 'zh',
    supportedLngs: ['zh'],
    // There's only one Chinese resource, so a missing key is truly missing -- no fallback, let it surface explicitly.
    //
    // Cost: keys missing on both sides render the key name itself in the UI. So check/fix copy
    // sent by the backend always carries `defaultValue` (see renderCheckMessage in
    // pages/Result.tsx), degrading to the original text instead of showing the key name when untranslated.
    fallbackLng: false,
    defaultNS: 'translation',
    load: 'languageOnly',
    // Tells i18next "I know resources: {} is empty — I'll addResourceBundle
    // later, don't look for a backend plugin." Required when using dynamic
    // imports to fill bundles.
    partialBundledLanguages: true,
    // Keep Suspense ON (default). main.tsx awaits initI18n before render
    // so resources are attached by first paint; Suspense then guards the
    // narrow race window inside i18next's init lifecycle that would
    // otherwise flash raw keys.
    interpolation: { escapeValue: false },
  });
  await packPromise;
  try {
    localStorage.setItem('i18nextLng', 'zh');
  } catch {
    /* noop */
  }
}

/**
 * Lazy-load a route-level namespace pack (`result` / `knowledge`).
 * Callers (useLoadNs) are unchanged.
 */
export async function loadNamespace(ns: 'result' | 'knowledge'): Promise<void> {
  await loadPack(ns, 'zh');
}

/**
 * Display label for a check category.
 *
 * Deliberately NOT `t('result.categoryLabels.' + category)`. Category strings
 * are backend section headers and contain i18next's separators: `:` in
 * "SEO: Title Tag" (nsSeparator — splits into a bogus namespace) and `.` in
 * "robots.txt" / "sitemap.xml" (keySeparator — splits into a nested path).
 * Both silently miss the entry. Passing the path as an ARRAY bypasses all
 * separator parsing.
 *
 * Falls back to the raw category so an unmapped label is merely untranslated.
 */
export function categoryLabel(category: string): string {
  const bundle = i18n.getResourceBundle('zh', 'translation') as
    | { result?: { categoryLabels?: Record<string, string> } }
    | undefined;
  const hit = bundle?.result?.categoryLabels?.[category];
  if (typeof hit === 'string' && hit) return hit;
  return category;
}

export default i18n;
