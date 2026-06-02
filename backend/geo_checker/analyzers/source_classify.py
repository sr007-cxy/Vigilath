"""Source type classification rules.

Classifies citation URLs into categories (wikipedia, news, blog, forum,
review, code, social, official, documentation, other) using domain lookup
and path-based heuristics.
"""

from __future__ import annotations

from urllib.parse import urlparse

# Exact domain → type mapping
_DOMAIN_MAP: dict[str, str] = {
    # Encyclopedia
    "wikipedia.org": "wikipedia",
    "wikidata.org": "wikipedia",
    "baike.baidu.com": "wikipedia",
    # Forums / Q&A
    "zhihu.com": "forum",
    "reddit.com": "forum",
    "stackoverflow.com": "forum",
    "quora.com": "forum",
    "v2ex.com": "forum",
    "segmentfault.com": "forum",
    "juejin.cn": "forum",
    # Code
    "github.com": "code",
    "gitlab.com": "code",
    "npmjs.com": "code",
    "pypi.org": "code",
    "crates.io": "code",
    # Reviews
    "g2.com": "review",
    "trustpilot.com": "review",
    "capterra.com": "review",
    "producthunt.com": "review",
    # Social
    "twitter.com": "social",
    "x.com": "social",
    "linkedin.com": "social",
    "facebook.com": "social",
    "weibo.com": "social",
    "weixin.qq.com": "social",
    # News — Chinese
    "36kr.com": "news",
    "new.qq.com": "news",
    "news.qq.com": "news",
    "thepaper.cn": "news",
    "ifanr.com": "news",
    "geekpark.net": "news",
    "ithome.com": "news",
    "cnbeta.com.tw": "news",
    # News — International
    "techcrunch.com": "news",
    "theverge.com": "news",
    "reuters.com": "news",
    "bloomberg.com": "news",
    "wired.com": "news",
    "arstechnica.com": "news",
    "thenextweb.com": "news",
    "venturebeat.com": "news",
    # Developer / Tech blogs (platforms)
    "dev.to": "blog",
    "medium.com": "blog",
    "hashnode.dev": "blog",
    "csdn.net": "blog",
    "cnblogs.com": "blog",
    "jianshu.com": "blog",
    # Education
    "edu.cn": "education",
    "edu": "education",
    # Video
    "youtube.com": "video",
    "bilibili.com": "video",
    # E-commerce
    "amazon.com": "ecommerce",
    "shopify.com": "ecommerce",
    "taobao.com": "ecommerce",
    "jd.com": "ecommerce",
}

# TLD suffixes to check (some domains have multi-part TLDs)
_TLD_MAP: dict[str, str] = {
    ".edu.cn": "education",
    ".edu": "education",
    ".gov.cn": "government",
    ".gov": "government",
}


def classify_source(url: str, target_domain: str = "") -> str:
    """Classify a citation URL into a source type.

    Args:
        url: The citation URL to classify.
        target_domain: The user's own domain (classified as 'official').

    Returns:
        One of: official, wikipedia, news, blog, forum, review, code, social,
        documentation, education, video, ecommerce, government, other.
    """
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.replace("www.", "").lower()
        path = parsed.path.lower()
    except Exception:
        return "other"

    # Own domain → official
    if target_domain and target_domain in domain:
        return "official"

    # Exact domain match
    for known_domain, stype in _DOMAIN_MAP.items():
        if domain == known_domain or domain.endswith("." + known_domain):
            return stype

    # TLD-based
    for tld, stype in _TLD_MAP.items():
        if domain.endswith(tld):
            return stype

    # Path-based heuristics
    if "/blog" in path or "/posts" in path or "/articles" in path:
        return "blog"
    if "/docs" in path or "/documentation" in path or "/reference" in path:
        return "documentation"
    if "/news" in path or "/press" in path or "/media" in path:
        return "news"
    if "/wiki" in path:
        return "wikipedia"
    if "/forum" in path or "/community" in path or "/discuss" in path:
        return "forum"

    return "other"


# Profound 风格大类:把上面的细类收敛成信源分析展示用的少数桶。
# 细类未列出的(code/review/documentation/education/government/official)统一落 other。
_BUCKET_MAP: dict[str, str] = {
    "news": "news",
    "social": "social",
    "wikipedia": "encyclopedia",
    "video": "video",
    "blog": "blog",
    "ecommerce": "ecommerce",
}

# 社区 vs 论坛 细分:classify_source 把这几类统一打成 "forum",这里按域名拆成两桶。
# community(社区)= 知识/问答/内容社区;forum(论坛)= 传统 BBS / 帖子站。
_COMMUNITY_DOMAINS: set[str] = {
    "zhihu.com", "quora.com", "stackoverflow.com", "juejin.cn",
    "segmentfault.com", "xiaohongshu.com", "douban.com",
}
_FORUM_DOMAINS: set[str] = {
    "reddit.com", "v2ex.com", "tieba.baidu.com", "hupu.com", "tianya.cn",
}


def classify_bucket(url: str, target_domain: str = "") -> str:
    """Classify a URL into a Profound-style coarse source bucket.

    Returns one of: owned, news, social, community, forum, encyclopedia,
    video, blog, ecommerce, other. ``owned`` is only returned when
    ``target_domain`` matches;callers 判定自有口径不同(如按 brand_keywords)
    可不传 target_domain 并自行叠加 owned。
    """
    fine = classify_source(url, target_domain)
    if fine == "official":
        return "owned"

    try:
        domain = urlparse(url).netloc.replace("www.", "").lower()
    except Exception:
        domain = ""

    def _hit(domset: set[str]) -> bool:
        return any(domain == d or domain.endswith("." + d) for d in domset)

    # 社区 / 论坛 细分(先按域名精确判,再用 classify_source 的 forum 兜底归社区)
    if _hit(_FORUM_DOMAINS):
        return "forum"
    if _hit(_COMMUNITY_DOMAINS) or fine == "forum":
        return "community"

    return _BUCKET_MAP.get(fine, "other")
