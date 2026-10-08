// The "single authoritative source" for engines/models -- every display / filter site-wide reads from here.
//
// Background: historically the engine list was scattered across 4 coexisting label systems + several hardcoded arrays + raw slugs output directly,
// so the same engine set sometimes showed 5, sometimes 10, sometimes in Chinese, sometimes English. This file consolidates into a single enum:
//   = the 6 models of the home page marketing lineup, with fixed Chinese/English names and order.
//
// Three conventions (consistent with product decisions):
//   1. After the ICP filing remediation (D1-a), only 6 domestic engines remain: DeepSeek / Tongyi Qianwen / Doubao /
//      Wenxin Yiyan / Yuanbao / Kimi. Global engine entries have been removed; don't add them back.
//   2. Only the "frontend display layer" is unified; backend data keys (qwen / wenxin etc.) are unchanged, so keys follow the backend data keys.
//   3. Yuanbao is displayed uniformly site-wide, no more "hidden on some pages, shown on others".
//
// key uses the backend data key -> engineLabel(dataKey) matches; slug uses the logo filename (may differ from key, e.g. qwen->tongyi).
// This enum doubles as the "whitelist": canonicalEngines() filters out any engine outside these 6.

import i18n from '../i18n';

export type EngineKey =
  | 'deepseek' | 'qwen' | 'doubao' | 'wenxin' | 'yuanbao' | 'kimi';

export interface EngineDef {
  key: EngineKey;
  zh: string;      // display name in the Chinese UI
  en: string;      // display name in the English UI
  slug: string;    // logo filename (/image/engines/{slug}.{ext})
  ext: string;     // logo extension
  sub_zh: string;  // vendor subtitle on home page cards (Chinese)
  sub_en: string;  // vendor subtitle on home page cards (English)
}

// Order = home page display order, used uniformly site-wide
export const ENGINES: EngineDef[] = [
  { key: 'deepseek',   zh: 'DeepSeek',   en: 'DeepSeek',   slug: 'deepseek',   ext: 'png', sub_zh: 'DeepSeek',   sub_en: 'DeepSeek' },
  { key: 'qwen',       zh: '通义千问',    en: 'Qwen',       slug: 'tongyi',     ext: 'svg', sub_zh: 'Tongyi',     sub_en: 'Alibaba' },
  { key: 'doubao',     zh: '豆包',        en: 'Doubao',     slug: 'doubao',     ext: 'png', sub_zh: 'Doubao',     sub_en: 'ByteDance' },
  { key: 'wenxin',     zh: '文心一言',    en: 'Wenxin',     slug: 'ernie',      ext: 'png', sub_zh: 'ERNIE',      sub_en: 'Baidu' },
  { key: 'yuanbao',    zh: '元宝',        en: 'Yuanbao',    slug: 'yuanbao',    ext: 'png', sub_zh: 'Yuanbao',    sub_en: 'Tencent' },
  { key: 'kimi',       zh: 'Kimi',       en: 'Kimi',       slug: 'kimi',       ext: 'png', sub_zh: 'Moonshot',   sub_en: 'Moonshot' },
];

export const ENGINE_KEYS: EngineKey[] = ENGINES.map(e => e.key);

const BY_KEY: Record<string, EngineDef> = Object.fromEntries(ENGINES.map(e => [e.key, e]));
const ORDER: Record<string, number> = Object.fromEntries(ENGINES.map((e, i) => [e.key, i]));

// Collection channel aliases -> the canonical key of the same model. doubao_browser is the "Doubao anonymous physical device browser
// channel", doubao_mobile is the mobile channel; both run the same Doubao model -- in display they must both be called Doubao.
// Without this alias layer, reports / task monitoring would output the raw key doubao_browser, and canonicalEngines would
// filter the whole column out as an off-list engine. ★ Only maps display identity; backend data keys are unchanged (see convention 2 in the file header).
const ALIAS: Record<string, EngineKey> = {
  doubao_browser: 'doubao',
  doubao_mobile: 'doubao',
  // Anonymous physical device channels for Wenxin / Qianwen (tasks claimed by V-networks/workers/desktop-cn), the same models as their originals
  wenxin_browser: 'wenxin',
  qwen_browser: 'qwen',
  // Yuanbao's physical device channel (2026-09-21). It's not anonymous -- the physical device also needs a login session from the rental account pool to run,
  // but in display it's still Yuanbao.
  yuanbao_browser: 'yuanbao',
  // Mobile App channel (2026-09-23, tasks claimed by V-networks/workers/emulator-cn, region=android):
  // collects each vendor's **native App** in the emulator. It still runs the same model, so display identity merges into the original;
  // "App or browser" is expressed separately by engineChannel(), not by the engine name.
  doubao_app: 'doubao',
  qwen_app: 'qwen',
  deepseek_app: 'deepseek',
  wenxin_app: 'wenxin',
  yuanbao_app: 'yuanbao',
  kimi_app: 'kimi',
  // diandian_app (Xiaohongshu Diandian) is deliberately not mapped: it's not a channel variant of these 6 models but a 7th engine.
  // To display it, it must first enter the ENGINES enum (along with changing the home page lineup); it can't sneak in via an alias.
};

// ── Client (channel) -- the same model can run on two clients ───────────────────────
//
// The "Monitoring & batch runs" panel shows two rows by client: one for browser, one for mobile; the same model can be checked in both rows,
// each counting as one Q&A (dispatch-center-side capacity / quality / rate limits are also computed separately per engine key).
//
//   web    browser -- collects each vendor's **web version**. DB keys may be the model name itself (server account pool),
//                     {model}_browser (office physical device browser) or doubao_mobile (Doubao mobile
//                     web, account pool forced into browser automation). Which one is chosen is up to ops; users only see "browser".
//   app    mobile  -- collects each vendor's **native App**; the key is always {model}_app, executed only by the emulator fleet.
//
// What users check and submit are the keys here (see backend user_selectable_engine_keys).
export type ChannelKey = 'web' | 'app';

export interface ChannelDef {
  key: ChannelKey;
  zh: string;
  en: string;
  hint_zh: string;   // control title, explaining what exactly this row collects
}

export const CHANNELS: ChannelDef[] = [
  { key: 'web', zh: '浏览器', en: 'Browser',
    hint_zh: '采各家网页版(服务器号池 / 办公室真机浏览器,由平台按产能选)' },
  { key: 'app', zh: '移动端', en: 'App',
    hint_zh: '采各家手机 App(安卓模拟器机群,与浏览器各算一次问答)' },
];

const APP_SUFFIX = '_app';

/** Which client an engine key runs on. {model}_app -> mobile, everything else (including _browser / doubao_mobile) -> browser. */
export function engineChannel(key: string): ChannelKey {
  return key.endsWith(APP_SUFFIX) ? 'app' : 'web';
}

/** Model + client -> the engine key submitted to the backend. The browser row uses the model name itself (the backend keeps the channel key ops chose). */
export function engineKeyFor(model: EngineKey, channel: ChannelKey): string {
  return channel === 'app' ? model + APP_SUFFIX : model;
}

/** Full identity like "Browser · Tongyi Qianwen", for places in reports / task monitoring that need to distinguish the client. */
export function engineChannelLabel(key: string): string {
  const ch = CHANNELS.find(c => c.key === engineChannel(key));
  const name = engineLabel(key);
  if (!ch) return name;
  return isEn() ? `${name} (${ch.en})` : `${ch.zh} · ${name}`;
}

/** Collection channel key -> canonical display key (non-aliases returned as-is). */
export function normalizeEngine(key: string): string {
  return ALIAS[key] ?? key;
}

function isEn(): boolean {
  return (i18n.language || 'zh').toLowerCase().startsWith('en');
}

/** Engine key -> display name in the current language; on a miss (non-canonical) fall back to the raw string, for easier troubleshooting. */
export function engineLabel(key: string): string {
  const d = BY_KEY[normalizeEngine(key)];
  if (!d) return key;
  return isEn() ? d.en : d.zh;
}

/** Whether it belongs to the unified 6 (whitelist check). */
export function isCanonicalEngine(key: string): boolean {
  return normalizeEngine(key) in BY_KEY;
}

/** Filter down to the 6 + sort in the unified order; anything off the list is removed. */
export function canonicalEngines<T extends string>(ids: readonly T[]): T[] {
  return ids.filter(e => normalizeEngine(e) in BY_KEY)
    .sort((a, b) => ORDER[normalizeEngine(a)] - ORDER[normalizeEngine(b)]);
}

/** Sort only, no filtering (unlisted ones go last) -- kept for scenarios that need to retain unknown items. */
export function sortEngines<T extends string>(ids: readonly T[]): T[] {
  const rank = (e: string) => {
    const k = normalizeEngine(e);
    return k in ORDER ? ORDER[k] : Number.MAX_SAFE_INTEGER;
  };
  return [...ids].sort((a, b) => rank(a) - rank(b));
}

// ── The four items below used to live in pages/BrandGrowth/lang.ts ──
// Brought back to the authoritative source after the brand growth module was retired. ENGINE_LABELS_* are for charts to look up labels directly
// (where you don't want to go through engineLabel for one label); visibleEngines is a historical name,
// semantically identical to canonicalEngines, kept to avoid changing every call site.

export const ENGINE_LABELS_ZH: Record<string, string> =
  Object.fromEntries(ENGINES.map(e => [e.key, e.zh]));
export const ENGINE_LABELS_EN: Record<string, string> =
  Object.fromEntries(ENGINES.map(e => [e.key, e.en]));

export const HIDDEN_ENGINES: string[] = [];

// Visible engines = filtered down to the unified 10 + sorted in the unified order (off-list collection keys are never shown).
export function visibleEngines<T extends string>(ids: readonly T[]): T[] {
  return canonicalEngines(ids);
}

// Unified order = authoritative source order (for places still referencing ENGINE_ORDER semantics).
export const ENGINE_ORDER: string[] = [...ENGINE_KEYS];
