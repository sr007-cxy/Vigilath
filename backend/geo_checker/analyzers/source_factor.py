"""GEO recommendation factor classification (FirstPageSage metric definition).

Assigns a cited source (url + title) to the GEO ranking factor it represents:
list_mention / reviews / directory / awards / customer / social / authority / other.

Different from source_classify (content type: news / social / video...) -- factors depend on the cited title/URL;
on the same media site a "2026 best CRM" list article counts as an "authoritative list mention", an ordinary report doesn't.

Reference: a breakdown of the FirstPageSage GEO recommendation algorithm.
"""

from __future__ import annotations

from urllib.parse import urlparse

# ── Domain sets (factors determined directly by domain) ──
_REVIEW_DOMAINS: set[str] = {
    "g2.com", "trustpilot.com", "capterra.com", "trustradius.com",
    "getapp.com", "softwareadvice.com", "yelp.com", "glassdoor.com",
    "sitejabber.com", "productreview.com.au", "dianping.com",  # Dianping
}
_DIRECTORY_DOMAINS: set[str] = {
    "crunchbase.com", "clutch.co", "goodfirms.co", "bbb.org", "dnb.com",
    "manta.com", "yellowpages.com", "yelp.com",  # yelp is also reviews; reviews take precedence (guaranteed by the order below)
    "tianyancha.com", "qcc.com", "aiqicha.baidu.com",  # Tianyancha / Qichacha / Aiqicha
    "wellfound.com", "angel.co", "builtwith.com",
    "baike.baidu.com", "wikipedia.org", "baike.so.com", "baike.sogou.com",  # encyclopedia entries
    "cbinsights.com", "preqin.com", "sec.gov", "maigoo.com", "chambers.com",
}
_SOCIAL_DOMAINS: set[str] = {
    "reddit.com", "x.com", "twitter.com", "linkedin.com", "facebook.com",
    "instagram.com", "weibo.com", "zhihu.com", "quora.com", "tiktok.com",
    "douyin.com", "xiaohongshu.com", "v2ex.com", "youtube.com", "bilibili.com",
    "medium.com", "substack.com", "smzdm.com", "xueqiu.com",
    "mp.weixin.qq.com", "zhidao.baidu.com",  # WeChat official accounts / Baidu Zhidao (UGC word of mouth)
}
# Three content-type classes (fallback when no factor keyword hits, shrinking the "other" share):
# Tech communities / developer content
_TECH_COMMUNITY_DOMAINS: set[str] = {
    "csdn.net", "51cto.com", "cnblogs.com", "php.cn", "juejin.cn",
    "segmentfault.com", "oschina.net", "infoq.cn", "jianshu.com",
    "icode.best", "jishuzhan.net", "aisharenet.com", "educity.cn",
    "xuehai.net", "stackoverflow.com", "github.com", "gitee.com",
    "cloud.tencent.com", "cloud.tencent.cn", "cloud.tencent.com.cn",
}
# News / industry media coverage
_NEWS_MEDIA_DOMAINS: set[str] = {
    "sina.cn", "sina.com.cn", "sohu.com", "163.com", "ithome.com",
    "36kr.com", "leiphone.com", "inews.qq.com", "eastmoney.com",
    "10jqka.com.cn", "cyzone.cn", "ikanchai.com", "sqrb.com.cn",
    "jjckb.cn", "doit.com.cn", "dostor.com", "eet-china.com",
    "china.com", "prnewswire.com", "trjcn.com", "zhineng.com.cn",
    "pconline.com.cn", "thepaper.cn", "jiemian.com", "cnbeta.com",
    "techcrunch.com", "theregister.com", "zdnet.com", "inewsweek.cn",
    "gmw.cn", "cena.com.cn", "chinadaily.com.cn", "enorth.com.cn",
    "ifeng.com", "nbd.com.cn", "news.qq.com", "ajupress.com",
    "c114.com.cn", "cri.cn", "hebnews.cn", "jingjiribao.cn", "cnr.cn",
    "geekpark.net", "it168.com", "itbrief.co.nz", "azertag.az",
    "bjd.com.cn", "hebccw.cn", "hbgrb.net", "hbjjrb.com", "cww.net.cn",
    "toutiao.com", "baijiahao.baidu.com",  # Toutiao / Baijiahao (self-media news)
}
# Vendor official sites and official materials (product pages / docs / solutions)
_VENDOR_DOMAINS: set[str] = {
    "aliyun.com", "huaweicloud.com", "h3c.com", "sangfor.com.cn",
    "intel.cn", "intel.com", "intel.com.tw", "gigabyte.com",
    "samsung.com", "ibm.com", "inspur.com", "ieisystem.com",
    "kove.com", "memverge.ai", "memverge.com", "juicefs.com",
    "worktile.com", "jiandaoyun.com", "pingcode.com", "bothstrong.com",
    "puyuetian.com", "saniffer.cn", "itangsoft.com", "vmware.com",
    "micron.com", "nvidia.com", "huawei.com", "microsoft.com",
    "aws.amazon.com", "asiainfo.com", "fusionlayer.com",
}
# Government / academic / industry institutions (mainly by domain suffix) -> site authority
_AUTHORITY_ORG_DOMAINS: set[str] = {
    "ceri.com.cn", "gdec.net.cn", "xiongan.gov.cn", "rmxiongan.com",
}
_AUTHORITY_SUFFIXES: tuple[str, ...] = (
    ".gov.cn", ".gov", ".edu.cn", ".org.cn", ".org.tw", ".ac.cn", ".org",
)

# ── Keyword sets (searched in title + URL path) ──
_LIST_KW: tuple[str, ...] = (
    "best ", "top ", "top10", "top 10", "leading", " vs ", "versus",
    "alternatives", "comparison", "compare", "roundup",
    "最佳", "十大", "排行", "排名", "榜", "盘点", "推荐", "选购", "对比",
)
_AWARD_KW: tuple[str, ...] = (
    "award", "winner", "accredit", "certified", "certification", "iso ",
    "recognized", "获奖", "奖项", "认证", "资质", "荣誉", "会员单位",
)
_CUSTOMER_KW: tuple[str, ...] = (
    "case study", "case-study", "case-studies", "success story", "usage data",
    "customers", "案例", "客户故事", "成功案例", "白皮书", "whitepaper",
)
_REVIEW_KW: tuple[str, ...] = (
    "review", "rating", "评价", "测评", "评测", "口碑", "评分",
)
_DIRECTORY_KW: tuple[str, ...] = (
    "directory", "listing", "目录", "名录", "黄页",
)


def _domain_hit(domain: str, domset: set[str]) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in domset)


def classify_factor(url: str, title: str = "", domain: str = "", owned: bool = False) -> str:
    """Assign a citation to a GEO factor.

    Args:
        url: citation URL.
        title: citation title (factor determination relies mainly on it).
        domain: pre-extracted domain (optional; parsed from url when omitted).
        owned: whether the domain matches the brand's own (match with no other signal -> authority).

    Returns:
        list_mention / reviews / directory / awards / customer / social /
        authority / other.
    """
    try:
        parsed = urlparse(url)
        dom = (domain or parsed.netloc).replace("www.", "").lower().strip().split(":")[0]
        path = parsed.path.lower()
    except Exception:
        dom, path = (domain or "").lower(), ""

    text = f"{title or ''} {path}".lower()

    # 1) Direct domain match (social / reviews / directories) -- reviews before directories (dual-attribute sites like yelp go to reviews)
    if _domain_hit(dom, _SOCIAL_DOMAINS):
        return "social"
    if _domain_hit(dom, _REVIEW_DOMAINS):
        return "reviews"
    if _domain_hit(dom, _DIRECTORY_DOMAINS):
        return "directory"

    # 2) Title/URL signal words (signals before owned, so list/case study/award pages are attributed correctly)
    if any(k in text for k in _AWARD_KW):
        return "awards"
    if any(k in text for k in _CUSTOMER_KW):
        return "customer"
    if any(k in text for k in _LIST_KW):
        return "list_mention"
    if any(k in text for k in _DIRECTORY_KW):
        return "directory"
    if any(k in text for k in _REVIEW_KW):
        return "reviews"

    # 3) Content-type fallback (shrink "other": tech communities / media coverage / vendor and official materials)
    if _domain_hit(dom, _TECH_COMMUNITY_DOMAINS):
        return "tech_community"
    if _domain_hit(dom, _NEWS_MEDIA_DOMAINS):
        return "news_media"
    if _domain_hit(dom, _VENDOR_DOMAINS):
        return "vendor"
    if _domain_hit(dom, _AUTHORITY_ORG_DOMAINS) or dom.endswith(_AUTHORITY_SUFFIXES):
        return "authority"
    # Media domain pattern fallback (news./epaper. prefixes are common in local media networks)
    if dom.startswith(("news.", "epaper.", "paper.")):
        return "news_media"

    # 4) Own domain -> site authority
    if owned:
        return "authority"

    return "other"
