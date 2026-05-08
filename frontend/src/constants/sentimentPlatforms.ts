// ⚠️ 已降级为兜底常量(2026-05-08 起)。
//
// Source of truth = backend `sentiment_platforms` 表(由 migration 010 seed),
// 通过 GET /api/sentiment/platforms 暴露,FE 走 useSentimentPlatforms() 拉取。
// 此文件只在两种场景被读:
//   (1) VITE_USE_MOCK_SENTIMENT=1 的 mock 模式
//   (2) /platforms 接口请求失败时的离线兜底(避免 UI 空白)
// 加 / 改平台请改 backend/migrations/010_sentiment_platforms.py 的 SEED 数组,
// 不要只改这里 — 否则线上(走 DB)和 mock(走这里)就会偏离。

export type PlatformCategory =
  | 'finance'   // 财经媒体
  | 'social'    // 社交媒体
  | 'forum'     // 论坛社区
  | 'video'     // 视频平台
  | 'news'      // 资讯门户
  | 'overseas'; // 海外

export type PlatformRegion = 'mainland' | 'hk' | 'overseas';

export interface PlatformEntry {
  code: string;
  domain: string;
  category: PlatformCategory;
  region: PlatformRegion;
}

// 顺序按"category 分组、组内按重要度"排,前端分组渲染时按 category 重新归类。
export const SENTIMENT_PLATFORMS: readonly PlatformEntry[] = [
  // ─── 财经媒体 ───
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

  // ─── 社交媒体 ───
  { code: 'weibo',        domain: 'weibo.com',            category: 'social',   region: 'mainland' },
  { code: 'weixin',       domain: 'mp.weixin.qq.com',     category: 'social',   region: 'mainland' },
  { code: 'xiaohongshu',  domain: 'xiaohongshu.com',      category: 'social',   region: 'mainland' },
  { code: 'zhihu',        domain: 'zhihu.com',            category: 'social',   region: 'mainland' },

  // ─── 论坛社区 ───
  { code: 'tieba',        domain: 'tieba.baidu.com',      category: 'forum',    region: 'mainland' },
  { code: 'zhidao',       domain: 'zhidao.baidu.com',     category: 'forum',    region: 'mainland' },
  { code: 'hupu',         domain: 'hupu.com',             category: 'forum',    region: 'mainland' },
  { code: 'douban',       domain: 'douban.com',           category: 'forum',    region: 'mainland' },

  // ─── 视频平台 ───
  { code: 'bilibili',     domain: 'bilibili.com',         category: 'video',    region: 'mainland' },
  { code: 'douyin',       domain: 'douyin.com',           category: 'video',    region: 'mainland' },
  { code: 'kuaishou',     domain: 'kuaishou.com',         category: 'video',    region: 'mainland' },
  { code: 'toutiao',      domain: 'toutiao.com',          category: 'video',    region: 'mainland' },

  // ─── 资讯门户 ───
  { code: 'baidu_news',   domain: 'news.baidu.com',       category: 'news',     region: 'mainland' },
  { code: 'ifeng',        domain: 'ifeng.com',            category: 'news',     region: 'mainland' },

  // ─── 海外 ───
  { code: 'reddit',       domain: 'reddit.com',           category: 'overseas', region: 'overseas' },
];

export const SENTIMENT_PLATFORM_CODES = SENTIMENT_PLATFORMS.map(p => p.code);

export const PLATFORM_CATEGORY_ORDER: readonly PlatformCategory[] = [
  'finance', 'social', 'forum', 'video', 'news', 'overseas',
];

/** 把任何带 `category` 字段的条目数组按 PLATFORM_CATEGORY_ORDER 分组。
 *  既能消费 PlatformEntry(本文件常量)又能消费 SentimentPlatform(API 返回),
 *  因为两者都满足 `{ category: string }`。 */
export function groupByCategory<T extends { category: string }>(
  entries: readonly T[],
): { category: PlatformCategory; items: T[] }[] {
  const buckets = new Map<string, T[]>();
  for (const e of entries) {
    const list = buckets.get(e.category) ?? [];
    list.push(e);
    buckets.set(e.category, list);
  }
  return PLATFORM_CATEGORY_ORDER
    .filter(c => buckets.has(c))
    .map(c => ({ category: c, items: buckets.get(c)! }));
}
