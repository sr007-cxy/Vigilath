// Unified publish platform list -- the publish center's single platform source of truth (display layer).
//
// label is written into the publish task's platform field, aligned with backend platform_rules.DEFAULT_PLATFORMS
// (a superset), so the platform value of created tasks hits platform rule injection; own_blog's aliases align with
// publish_task_service.AUTO_PLATFORMS.
// key is a normalized identifier, reserved for future backend platform normalization; normalizePlatform only does display-layer
// matching (mapping free-text platform names recommended by source_strategy to list entries).
//
// TODO: the mock CHANNELS in Posts.tsx and PLATFORM_OPTIONS in TemplateDrawer are historical
// dual sources of truth, to be consolidated here later.

export type PlatformKey =
  | 'own_blog' | 'gongzhonghao' | 'zhihu' | 'xiaohongshu' | 'weibo'
  | 'douyin' | 'shipinhao' | 'sohu' | 'baijia' | 'toutiao'
  | 'csdn' | 'smzdm' | 'cnblogs' | 'rwj' | 'mjk';

export type PlatformGroup = 'owned' | 'social' | 'media_account' | 'paid';
export type PublishKind = 'auto_mediumsly' | 'manual' | 'paid_rwj';

export interface PlatformEntry {
  key: PlatformKey;
  label: string;             // Chinese platform name = task table platform field value
  group: PlatformGroup;
  publishKind: PublishKind;
  aliases: string[];         // historical free text (compared lowercase) -> this entry
  staffOnly?: boolean;
}

export const PLATFORM_GROUP_LABEL: Record<PlatformGroup, string> = {
  owned: '自有渠道',
  social: '社交内容平台',
  media_account: '号媒体',
  paid: '付费媒体',
};

export const PLATFORM_CATALOG: PlatformEntry[] = [
  { key: 'own_blog', label: '官网博客', group: 'owned', publishKind: 'auto_mediumsly',
    aliases: ['官网', '官网博客', '自有站点', 'mediumsly'], staffOnly: true },
  { key: 'gongzhonghao', label: '公众号', group: 'social', publishKind: 'manual',
    aliases: ['公众号', '微信公众号', 'wxgzh', 'weixin'] },
  { key: 'zhihu', label: '知乎', group: 'social', publishKind: 'manual',
    aliases: ['知乎', 'zhihu', 'zhihu.com'] },
  { key: 'xiaohongshu', label: '小红书', group: 'social', publishKind: 'manual',
    aliases: ['小红书', 'xiaohongshu', 'xhs'] },
  { key: 'weibo', label: '微博', group: 'social', publishKind: 'manual',
    aliases: ['微博', 'weibo', 'weibo.com'] },
  { key: 'douyin', label: '抖音', group: 'social', publishKind: 'manual',
    aliases: ['抖音', 'douyin'] },
  { key: 'shipinhao', label: '视频号', group: 'social', publishKind: 'manual',
    aliases: ['视频号', '微信视频号'] },
  { key: 'sohu', label: '搜狐号', group: 'media_account', publishKind: 'manual',
    aliases: ['搜狐号', '搜狐', 'sohu', 'sohu.com', 'm.sohu.com'] },
  { key: 'baijia', label: '百家号', group: 'media_account', publishKind: 'manual',
    aliases: ['百家号', 'baijiahao', 'baijiahao.baidu.com'] },
  { key: 'toutiao', label: '头条号', group: 'media_account', publishKind: 'manual',
    aliases: ['头条号', '今日头条', 'toutiao', 'toutiao.com'] },
  { key: 'csdn', label: 'CSDN', group: 'media_account', publishKind: 'manual',
    aliases: ['csdn', 'csdn.net', 'blog.csdn.net'] },
  { key: 'smzdm', label: '什么值得买', group: 'media_account', publishKind: 'manual',
    aliases: ['什么值得买', 'smzdm', 'smzdm.com'] },
  { key: 'cnblogs', label: '博客园', group: 'media_account', publishKind: 'manual',
    aliases: ['博客园', 'cnblogs', 'cnblogs.com'] },
  // The two paid sources are **deliberately not merged into one**: label is written into the publish task's platform field,
  // and backend _task_provider / rwj_order_poller / rwj_liveness all rely on this Chinese name to look up the site.
  // Merging = lookup fails = money charged without getting the URL back. De-vendoring on the user side relies on staffOnly (they never appear in the
  // manual platform selection grid) + the task output's platform_display, not on changing this.
  { key: 'rwj', label: '软文匠', group: 'paid', publishKind: 'paid_rwj',
    aliases: ['软文匠'], staffOnly: true },
  { key: 'mjk', label: '媒介库', group: 'paid', publishKind: 'paid_rwj',
    aliases: ['媒介库'], staffOnly: true },
];

/** Free-text platform name -> list entry (lowercase substring match on aliases); returns null on a miss. */
export function normalizePlatform(freeText: string): PlatformEntry | null {
  const s = (freeText || '').trim().toLowerCase();
  if (!s) return null;
  for (const entry of PLATFORM_CATALOG) {
    for (const a of entry.aliases) {
      const al = a.toLowerCase();
      if (s === al || s.includes(al)) return entry;
    }
  }
  return null;
}
