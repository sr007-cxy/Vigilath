// 与 services/sentinel-service/search/plan.py 的 PLATFORM_CATALOG 对齐。
// 后端 monitoring plan 真正发查询的平台清单 — FE 既用于文章页媒体筛选,
// 也用于品牌设置页的媒体白名单编辑器。修改时记得同步改 plan.py。

export interface PlatformEntry {
  code: string;
  domain: string;
}

export const SENTIMENT_PLATFORMS: readonly PlatformEntry[] = [
  { code: 'xueqiu',      domain: 'xueqiu.com' },
  { code: 'eastmoney',   domain: 'guba.eastmoney.com' },
  { code: 'weibo',       domain: 'weibo.com' },
  { code: 'zhihu',       domain: 'zhihu.com' },
  { code: '36kr',        domain: '36kr.com' },
  { code: 'caixin',      domain: 'caixin.com' },
  { code: 'bilibili',    domain: 'bilibili.com' },
  { code: 'toutiao',     domain: 'toutiao.com' },
  { code: 'weixin',      domain: 'mp.weixin.qq.com' },
  { code: 'xiaohongshu', domain: 'xiaohongshu.com' },
  { code: 'kuaishou',    domain: 'kuaishou.com' },
  { code: 'tieba',       domain: 'tieba.baidu.com' },
  { code: 'zhidao',      domain: 'zhidao.baidu.com' },
  { code: 'baidu_news',  domain: 'news.baidu.com' },
  { code: 'reddit',      domain: 'reddit.com' },
];

export const SENTIMENT_PLATFORM_CODES = SENTIMENT_PLATFORMS.map(p => p.code);
