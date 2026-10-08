// Cited sources -> platform aggregation: domains normalized to platforms (Sohu/Toutiao/Zhihu...), so the report center's source ranking can show by platform.
// Platform logos come directly from the platform site's favicon (reachable in CN, not via google s2); on load failure the UI falls back to a first-letter badge.

export interface PlatformMeta {
  zh: string;
  en: string;
  /** Host to fetch the favicon from; defaults to www.<registrable domain> */
  logoHost?: string;
}

// Match on the full hostname first (after stripping www./m.), then on the registrable domain -- Baidu/Tencent subdomains are different products and must be distinguished
const HOST_MAP: Record<string, PlatformMeta> = {
  'baijiahao.baidu.com': { zh: '百家号', en: 'Baijiahao', logoHost: 'baijiahao.baidu.com' },
  'baike.baidu.com': { zh: '百度百科', en: 'Baidu Baike', logoHost: 'baike.baidu.com' },
  'zhidao.baidu.com': { zh: '百度知道', en: 'Baidu Zhidao', logoHost: 'zhidao.baidu.com' },
  'tieba.baidu.com': { zh: '百度贴吧', en: 'Baidu Tieba', logoHost: 'tieba.baidu.com' },
  'wenku.baidu.com': { zh: '百度文库', en: 'Baidu Wenku', logoHost: 'wenku.baidu.com' },
  'mp.weixin.qq.com': { zh: '微信公众号', en: 'WeChat OA', logoHost: 'mp.weixin.qq.com' },
  'news.qq.com': { zh: '腾讯新闻', en: 'Tencent News', logoHost: 'news.qq.com' },
  'zhuanlan.zhihu.com': { zh: '知乎', en: 'Zhihu', logoHost: 'www.zhihu.com' },
  'finance.sina.com.cn': { zh: '新浪财经', en: 'Sina Finance', logoHost: 'finance.sina.com.cn' },
};

const DOMAIN_MAP: Record<string, PlatformMeta> = {
  'sohu.com': { zh: '搜狐', en: 'Sohu' },
  'toutiao.com': { zh: '头条', en: 'Toutiao' },
  'zhihu.com': { zh: '知乎', en: 'Zhihu' },
  'baidu.com': { zh: '百度', en: 'Baidu' },
  'qq.com': { zh: '腾讯', en: 'Tencent' },
  'sina.com.cn': { zh: '新浪', en: 'Sina' },
  'weibo.com': { zh: '微博', en: 'Weibo' },
  'weibo.cn': { zh: '微博', en: 'Weibo', logoHost: 'weibo.com' },
  '163.com': { zh: '网易', en: 'NetEase' },
  'csdn.net': { zh: 'CSDN', en: 'CSDN' },
  'bilibili.com': { zh: '哔哩哔哩', en: 'Bilibili' },
  'thepaper.cn': { zh: '澎湃新闻', en: 'The Paper' },
  'ifeng.com': { zh: '凤凰网', en: 'iFeng' },
  '36kr.com': { zh: '36氪', en: '36Kr' },
  'huxiu.com': { zh: '虎嗅', en: 'Huxiu' },
  'xiaohongshu.com': { zh: '小红书', en: 'RedNote' },
  'douban.com': { zh: '豆瓣', en: 'Douban' },
  'jianshu.com': { zh: '简书', en: 'Jianshu' },
  'juejin.cn': { zh: '掘金', en: 'Juejin' },
  'cnblogs.com': { zh: '博客园', en: 'Cnblogs' },
  'douyin.com': { zh: '抖音', en: 'Douyin' },
  'kuaishou.com': { zh: '快手', en: 'Kuaishou' },
  'xueqiu.com': { zh: '雪球', en: 'Xueqiu' },
  'eastmoney.com': { zh: '东方财富', en: 'Eastmoney' },
  'jrj.com.cn': { zh: '金融界', en: 'JRJ' },
  'hexun.com': { zh: '和讯', en: 'Hexun' },
  'caixin.com': { zh: '财新', en: 'Caixin' },
  'yicai.com': { zh: '第一财经', en: 'Yicai' },
  'people.com.cn': { zh: '人民网', en: 'People.cn' },
  'xinhuanet.com': { zh: '新华网', en: 'Xinhuanet' },
  'chinanews.com': { zh: '中国新闻网', en: 'ChinaNews' },
  'chinanews.com.cn': { zh: '中国新闻网', en: 'ChinaNews', logoHost: 'www.chinanews.com.cn' },
  'huanqiu.com': { zh: '环球网', en: 'Huanqiu' },
  'cctv.com': { zh: '央视网', en: 'CCTV' },
  'gmw.cn': { zh: '光明网', en: 'GMW' },
  'jiemian.com': { zh: '界面新闻', en: 'Jiemian' },
  'tmtpost.com': { zh: '钛媒体', en: 'TMTPost' },
  'iyiou.com': { zh: '亿欧', en: 'EqualOcean' },
  'zhongguowangshi.com': { zh: '中国网', en: 'China.com.cn' },
  'china.com.cn': { zh: '中国网', en: 'China.com.cn' },
  'wikipedia.org': { zh: '维基百科', en: 'Wikipedia', logoHost: 'zh.wikipedia.org' },
  'github.com': { zh: 'GitHub', en: 'GitHub' },
};

// Common two-part public suffixes -- the registrable domain takes one more level (sina.com.cn rather than com.cn)
const TWO_PART_TLDS = new Set([
  'com.cn', 'net.cn', 'org.cn', 'gov.cn', 'edu.cn', 'ac.cn',
  'com.hk', 'com.tw', 'co.jp', 'co.uk', 'com.sg',
]);

/** Hostname with the www./m. prefix stripped */
export function normalizeHost(domain: string): string {
  return domain.toLowerCase().replace(/^(www|m)\./, '');
}

/** Registrable domain: zhuanlan.zhihu.com -> zhihu.com; finance.sina.com.cn -> sina.com.cn */
export function registrableDomain(host: string): string {
  const parts = host.split('.');
  if (parts.length <= 2) return host;
  const lastTwo = parts.slice(-2).join('.');
  const n = TWO_PART_TLDS.has(lastTwo) ? 3 : 2;
  return parts.slice(-n).join('.');
}

export interface PlatformInfo {
  /** Aggregation key */
  key: string;
  zh: string;
  en: string;
  /** favicon URL */
  logo: string;
}

export function platformOf(domain: string): PlatformInfo {
  const host = normalizeHost(domain);
  const reg = registrableDomain(host);
  const meta = HOST_MAP[host];
  if (meta) {
    return {
      key: meta.zh, zh: meta.zh, en: meta.en,
      logo: `https://${meta.logoHost || host}/favicon.ico`,
    };
  }
  const dMeta = DOMAIN_MAP[reg];
  if (dMeta) {
    return {
      key: dMeta.zh, zh: dMeta.zh, en: dMeta.en,
      logo: `https://${dMeta.logoHost || `www.${reg}`}/favicon.ico`,
    };
  }
  // Unlisted platforms: use the registrable domain as the name and fetch the favicon from the original host (subdomain sites often have favicons only under their own host)
  return { key: reg, zh: reg, en: reg, logo: `https://${host}/favicon.ico` };
}
