// ⚠️ Downgraded to fallback constants (since 2026-05-08).
//
// Source of truth = backend `sentiment_platforms` table (seeded by migration 010),
// exposed via GET /api/sentiment/platforms; the FE fetches it with useSentimentPlatforms().
// This file is only read in two scenarios:
//   (1) mock mode with VITE_USE_MOCK_SENTIMENT=1
//   (2) offline fallback when the /platforms request fails (to avoid a blank UI)
// To add / change platforms, edit the SEED array in backend/migrations/010_sentiment_platforms.py,
// not just here -- otherwise production (via DB) and mock (via here) will diverge.

// ICP filing remediation: the fallback constants no longer have overseas platforms; the types still keep 'overseas' / 'overseas_en',
// because the backend sentiment_platforms table may still have historical rows -- useSentimentPlatforms()
// uniformly filters out region === 'overseas', ensuring no overseas sites appear in the UI.
//
// Legacy single axis (introduced in 010; the frontend no longer groups by it; kept as a field for compatibility with old fallback data)
export type PlatformCategory =
  | 'finance' | 'social' | 'forum' | 'video' | 'news' | 'overseas';

// Two orthogonal axes since 011 -- the UI draws filters from these two
export type MediaType =
  | 'web' | 'weibo' | 'wechat' | 'app' | 'forum'
  | 'short_video' | 'long_video' | 'broadcast'
  | 'newspaper' | 'magazine' | 'blog' | 'ecommerce' | 'review';

export type Industry =
  | 'finance' | 'general' | 'tech' | 'science'
  | 'party_media' | 'health' | 'overseas_en';

export type PlatformRegion = 'mainland' | 'hk' | 'overseas';

export interface PlatformEntry {
  code: string;
  domain: string;
  category: PlatformCategory;
  media_type: MediaType;
  industry: Industry;
  region: PlatformRegion;
}

/** code -> (media_type, industry) -- aligned with AXES_MAP in backend/migrations/011.
 *  The constant array only hand-maintains (category, region); the other two axes are derived from this table at module load,
 *  reducing row data duplication; consistent with the fields returned by backend/api. */
const AXES_MAP: Record<string, [MediaType, Industry]> = {
  xueqiu:       ['app',         'finance'],
  eastmoney:    ['forum',       'finance'],
  caixin:       ['web',         'finance'],
  '36kr':       ['web',         'tech'],
  sina_finance: ['web',         'finance'],
  qq_finance:   ['web',         'finance'],
  '163_finance':['web',         'finance'],
  ths:          ['app',         'finance'],
  wallstreetcn: ['web',         'finance'],
  yicai:        ['web',         'finance'],
  cls:          ['app',         'finance'],
  gelonghui:    ['web',         'finance'],
  jrj:          ['web',         'finance'],
  hexun:        ['web',         'finance'],
  jin10:        ['web',         'finance'],
  nbd:          ['web',         'finance'],
  jingji21:     ['web',         'finance'],
  stcn:         ['web',         'finance'],
  cs_com:       ['web',         'finance'],
  cnstock:      ['web',         'finance'],
  p5w:          ['web',         'finance'],
  caijing:      ['web',         'finance'],
  iyiou:        ['web',         'tech'],
  futunn:       ['app',         'finance'],
  itiger:       ['app',         'finance'],
  zhitong:      ['web',         'finance'],
  weibo:        ['weibo',       'general'],
  weixin:       ['wechat',      'general'],
  xiaohongshu:  ['app',         'general'],
  zhihu:        ['app',         'general'],
  tieba:        ['forum',       'general'],
  zhidao:       ['forum',       'general'],
  hupu:         ['forum',       'general'],
  douban:       ['forum',       'general'],
  v2ex:         ['forum',       'tech'],
  guokr:        ['forum',       'science'],
  csdn:         ['forum',       'tech'],
  juejin:       ['forum',       'tech'],
  bilibili:     ['long_video',  'general'],
  douyin:       ['short_video', 'general'],
  kuaishou:     ['short_video', 'general'],
  toutiao:      ['app',         'general'],
  vqq:          ['long_video',  'general'],
  iqiyi:        ['long_video',  'general'],
  ixigua:       ['short_video', 'general'],
  baidu_news:   ['web',         'general'],
  ifeng:        ['web',         'general'],
  sina_main:    ['web',         'general'],
  sohu:         ['web',         'general'],
  chinanews:    ['web',         'party_media'],
  xinhuanet:    ['web',         'party_media'],
  people:       ['web',         'party_media'],
  huanqiu:      ['web',         'party_media'],
  guancha:      ['web',         'general'],
  ce_cn:        ['web',         'party_media'],
  thepaper:     ['web',         'general'],
  jiemian:      ['web',         'finance'],
  huxiu:        ['web',         'tech'],
  tmtpost:      ['web',         'tech'],
  pingwest:     ['web',         'tech'],
  ifanr:        ['web',         'tech'],
  qbitai:       ['web',         'tech'],
  jiqizhixin:   ['web',         'tech'],
};

interface RawEntry {
  code: string;
  domain: string;
  category: PlatformCategory;
  region: PlatformRegion;
}

function deriveAxes(entry: RawEntry): PlatformEntry {
  const [mt, ind] = AXES_MAP[entry.code] ?? ['web', 'general'];
  return { ...entry, media_type: mt, industry: ind };
}

// Ordered as "grouped by category, by importance within a group"; the frontend regroups by media_type / industry when rendering.
const RAW_ENTRIES: readonly RawEntry[] = [
  // ─── Financial media (core) ───
  { code: 'xueqiu',       domain: 'xueqiu.com',           category: 'finance',  region: 'mainland' },
  { code: 'eastmoney',    domain: 'guba.eastmoney.com',   category: 'finance',  region: 'mainland' },
  { code: 'caixin',       domain: 'caixin.com',           category: 'finance',  region: 'mainland' },
  { code: '36kr',         domain: '36kr.com',             category: 'finance',  region: 'mainland' },
  { code: 'sina_finance', domain: 'finance.sina.com.cn',  category: 'finance',  region: 'mainland' },
  { code: 'qq_finance',   domain: 'finance.qq.com',       category: 'finance',  region: 'mainland' },
  { code: '163_finance',  domain: 'money.163.com',        category: 'finance',  region: 'mainland' },
  { code: 'ths',          domain: '10jqka.com.cn',        category: 'finance',  region: 'mainland' },
  { code: 'wallstreetcn', domain: 'wallstreetcn.com',     category: 'finance',  region: 'mainland' },
  { code: 'yicai',        domain: 'yicai.com',            category: 'finance',  region: 'mainland' },
  { code: 'cls',          domain: 'cls.cn',               category: 'finance',  region: 'mainland' },
  { code: 'gelonghui',    domain: 'gelonghui.com',        category: 'finance',  region: 'hk'       },
  // ─── Financial media (extended) ───
  { code: 'jrj',          domain: 'jrj.com.cn',           category: 'finance',  region: 'mainland' },
  { code: 'hexun',        domain: 'hexun.com',            category: 'finance',  region: 'mainland' },
  { code: 'jin10',        domain: 'jin10.com',            category: 'finance',  region: 'mainland' },
  { code: 'nbd',          domain: 'nbd.com.cn',           category: 'finance',  region: 'mainland' },
  { code: 'jingji21',     domain: '21jingji.com',         category: 'finance',  region: 'mainland' },
  { code: 'stcn',         domain: 'stcn.com',             category: 'finance',  region: 'mainland' },
  { code: 'cs_com',       domain: 'cs.com.cn',            category: 'finance',  region: 'mainland' },
  { code: 'cnstock',      domain: 'cnstock.com',          category: 'finance',  region: 'mainland' },
  { code: 'p5w',          domain: 'p5w.net',              category: 'finance',  region: 'mainland' },
  { code: 'caijing',      domain: 'caijing.com.cn',       category: 'finance',  region: 'mainland' },
  { code: 'iyiou',        domain: 'iyiou.com',            category: 'finance',  region: 'mainland' },
  { code: 'futunn',       domain: 'futunn.com',           category: 'finance',  region: 'mainland' },
  { code: 'itiger',       domain: 'itiger.com',           category: 'finance',  region: 'mainland' },
  { code: 'zhitong',      domain: 'zhitongcaijing.com',   category: 'finance',  region: 'hk'       },

  // ─── Social media ───
  { code: 'weibo',        domain: 'weibo.com',            category: 'social',   region: 'mainland' },
  { code: 'weixin',       domain: 'mp.weixin.qq.com',     category: 'social',   region: 'mainland' },
  { code: 'xiaohongshu',  domain: 'xiaohongshu.com',      category: 'social',   region: 'mainland' },
  { code: 'zhihu',        domain: 'zhihu.com',            category: 'social',   region: 'mainland' },

  // ─── Forums & communities ───
  { code: 'tieba',        domain: 'tieba.baidu.com',      category: 'forum',    region: 'mainland' },
  { code: 'zhidao',       domain: 'zhidao.baidu.com',     category: 'forum',    region: 'mainland' },
  { code: 'hupu',         domain: 'hupu.com',             category: 'forum',    region: 'mainland' },
  { code: 'douban',       domain: 'douban.com',           category: 'forum',    region: 'mainland' },
  { code: 'v2ex',         domain: 'v2ex.com',             category: 'forum',    region: 'mainland' },
  { code: 'guokr',        domain: 'guokr.com',            category: 'forum',    region: 'mainland' },
  { code: 'csdn',         domain: 'csdn.net',             category: 'forum',    region: 'mainland' },
  { code: 'juejin',       domain: 'juejin.cn',            category: 'forum',    region: 'mainland' },

  // ─── Video platforms ───
  { code: 'bilibili',     domain: 'bilibili.com',         category: 'video',    region: 'mainland' },
  { code: 'douyin',       domain: 'douyin.com',           category: 'video',    region: 'mainland' },
  { code: 'kuaishou',     domain: 'kuaishou.com',         category: 'video',    region: 'mainland' },
  { code: 'toutiao',      domain: 'toutiao.com',          category: 'video',    region: 'mainland' },
  { code: 'vqq',          domain: 'v.qq.com',             category: 'video',    region: 'mainland' },
  { code: 'iqiyi',        domain: 'iqiyi.com',            category: 'video',    region: 'mainland' },
  { code: 'ixigua',       domain: 'ixigua.com',           category: 'video',    region: 'mainland' },

  // ─── News portals ───
  { code: 'baidu_news',   domain: 'news.baidu.com',       category: 'news',     region: 'mainland' },
  { code: 'ifeng',        domain: 'ifeng.com',            category: 'news',     region: 'mainland' },
  { code: 'sina_main',    domain: 'sina.com.cn',          category: 'news',     region: 'mainland' },
  { code: 'sohu',         domain: 'sohu.com',             category: 'news',     region: 'mainland' },
  { code: 'chinanews',    domain: 'chinanews.com',        category: 'news',     region: 'mainland' },
  { code: 'xinhuanet',    domain: 'xinhuanet.com',        category: 'news',     region: 'mainland' },
  { code: 'people',       domain: 'people.com.cn',        category: 'news',     region: 'mainland' },
  { code: 'huanqiu',      domain: 'huanqiu.com',          category: 'news',     region: 'mainland' },
  { code: 'guancha',      domain: 'guancha.cn',           category: 'news',     region: 'mainland' },
  { code: 'ce_cn',        domain: 'ce.cn',                category: 'news',     region: 'mainland' },
  { code: 'thepaper',     domain: 'thepaper.cn',          category: 'news',     region: 'mainland' },
  { code: 'jiemian',      domain: 'jiemian.com',          category: 'news',     region: 'mainland' },
  { code: 'huxiu',        domain: 'huxiu.com',            category: 'news',     region: 'mainland' },
  { code: 'tmtpost',      domain: 'tmtpost.com',          category: 'news',     region: 'mainland' },
  { code: 'pingwest',     domain: 'pingwest.com',         category: 'news',     region: 'mainland' },
  { code: 'ifanr',        domain: 'ifanr.com',            category: 'news',     region: 'mainland' },
  { code: 'qbitai',       domain: 'qbitai.com',           category: 'news',     region: 'mainland' },
  { code: 'jiqizhixin',   domain: 'jiqizhixin.com',       category: 'news',     region: 'mainland' },
];

export const SENTIMENT_PLATFORMS: readonly PlatformEntry[] = RAW_ENTRIES.map(deriveAxes);

export const SENTIMENT_PLATFORM_CODES = SENTIMENT_PLATFORMS.map(p => p.code);

export const PLATFORM_CATEGORY_ORDER: readonly PlatformCategory[] = [
  'finance', 'social', 'forum', 'video', 'news',
];

// Since 011 UI rendering prefers these two axes
export const MEDIA_TYPE_ORDER: readonly MediaType[] = [
  'web', 'weibo', 'wechat', 'app', 'forum',
  'short_video', 'long_video', 'broadcast',
  'newspaper', 'magazine', 'blog', 'ecommerce', 'review',
];

export const INDUSTRY_ORDER: readonly Industry[] = [
  'finance', 'tech', 'general', 'science', 'party_media', 'health', 'overseas_en',
];

/** Legacy (single category axis) -- still called by code not yet migrated. */
export function groupByCategory<T extends { category?: string }>(
  entries: readonly T[],
): { category: PlatformCategory; items: T[] }[] {
  const buckets = new Map<string, T[]>();
  for (const e of entries) {
    const c = e.category;
    if (!c) continue;
    const list = buckets.get(c) ?? [];
    list.push(e);
    buckets.set(c, list);
  }
  return PLATFORM_CATEGORY_ORDER
    .filter(c => buckets.has(c))
    .map(c => ({ category: c, items: buckets.get(c)! }));
}

/** Group by media_type (WisersOne's "media type" axis). */
export function groupByMediaType<T extends { media_type: string }>(
  entries: readonly T[],
): { mediaType: MediaType; items: T[] }[] {
  const buckets = new Map<string, T[]>();
  for (const e of entries) {
    const list = buckets.get(e.media_type) ?? [];
    list.push(e);
    buckets.set(e.media_type, list);
  }
  return MEDIA_TYPE_ORDER
    .filter(m => buckets.has(m))
    .map(m => ({ mediaType: m, items: buckets.get(m)! }));
}
