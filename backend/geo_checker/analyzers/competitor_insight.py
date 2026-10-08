"""Competitor insight analyzer -- How do competitors appear in AI answers?

Extracts competitor mentions from BOTH answer text and citation URLs,
performs framing analysis, and generates a competitive landscape overview.

Key insight: Many AI engines (especially via API) don't return structured
citation URLs but DO mention competitor brands by name in the answer text.
So text-level brand extraction is the PRIMARY signal, not citations.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse


# Domains to exclude from competitor analysis (platforms, not brands)
_PLATFORM_DOMAINS = {
    "google.com", "youtube.com", "wikipedia.org", "reddit.com",
    "twitter.com", "x.com", "facebook.com", "linkedin.com",
    "medium.com", "github.com", "stackoverflow.com", "quora.com",
    "amazon.com", "yelp.com", "bbb.org", "trustpilot.com",
    "zhihu.com", "baidu.com", "weibo.com", "bilibili.com",
    "csdn.net", "cnblogs.com", "jianshu.com", "juejin.cn",
    "dev.to", "v2ex.com", "segmentfault.com", "g2.com",
    "capterra.com", "producthunt.com",
    # CN content/media platforms -- appearing in citations only means being indexed, not a competitor
    "sohu.com", "qq.com", "163.com", "sina.com.cn", "sina.cn",
    "toutiao.com", "xiaohongshu.com", "douyin.com", "douban.com",
    "sogou.com", "so.com", "ifeng.com", "thepaper.cn", "36kr.com",
    "huxiu.com", "xinhuanet.com", "people.com.cn", "cctv.com",
    "apple.com", "zol.com.cn", "pconline.com.cn", "cnmo.com",
}

# Generic subdomains/prefixes of cited domains -- not brands (the m in m.sohu.com, the news in news.qq.com)
_GENERIC_DOMAIN_LABELS = {
    "www", "m", "wap", "mobile", "news", "app", "apps", "web", "mp",
    "i", "e", "ex", "blog", "shop", "mall", "store", "docs", "wiki",
    "bbs", "forum", "static", "img", "cdn", "api", "open", "my",
    "go", "s", "t", "a", "b", "c", "cn", "en", "zh", "help", "info",
}

# Multi-part public suffixes -- skipped when taking the registered domain's main label (m.sohu.com.cn -> sohu)
_MULTI_TLDS = {
    "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn",
    "com.hk", "com.tw", "co.jp", "co.uk", "com.sg",
}


def _registrable_label(domain: str) -> str:
    """Take the "registered domain main label" of a cited domain as a brand candidate: m.sohu.com -> sohu,
    news.qq.com -> qq. The old logic split(".")[0] treated subdomains (m/news/app) as brands,
    and noise like M / News / Apps showed up in the competitor ranking."""
    parts = [p for p in domain.lower().split(".") if p]
    if len(parts) >= 3 and ".".join(parts[-2:]) in _MULTI_TLDS:
        return parts[-3]
    if len(parts) >= 2:
        return parts[-2]
    return parts[0] if parts else ""


# Common section headings / category words in Chinese answers -- not brand names when bolded
_ZH_STOPWORDS = {
    "核心优势", "产品特点", "主要功能", "总结", "建议", "优势", "劣势",
    "对比", "品牌", "平台", "功能", "价格", "服务", "用户", "场景",
    "注意", "提示", "结论", "综上", "首先", "其次", "最后", "另外",
    "推荐", "评价", "口碑", "特点", "亮点", "缺点", "优点", "适合人群",
    "使用体验", "性价比", "安全性", "专业性", "权威性", "知名度",
    "市场定位", "发展历程", "公司背景", "注意事项", "温馨提示",
    "参考来源", "相关推荐", "热门产品", "产品线", "解决方案",
    # Common verbs/phrases (competitor ranking noise measured 2026-07-30: 配合/效果因人而异/操作 ("cooperate / results vary / operation"))
    "配合", "搭配", "坚持", "效果", "此外", "同时", "其中", "例如", "操作",
    "含有", "适量运动", "均衡饮食", "控制饮食", "配合运动", "搭配运动",
    "使用方法", "服用方法", "副作用", "禁忌", "原理", "成分",
    "适用人群", "特别提醒", "温馨提醒", "重要提示",
    # Category/nutrition words and pronoun phrases (measured noise: 优质蛋白/健康脂肪/如果你/社区互动 ("quality protein / healthy fats / if you / community interaction"))
    "优质蛋白", "健康脂肪", "膳食纤维", "碳水化合物", "蛋白质",
    "生酮减肥", "轻断食", "社区互动", "专业指导", "如果你", "需要注意", "个体差异",
    # Generic adjectives/verbs and section words (measured noise: 适合 93%/核心功能/使用前 ("suitable 93% / core features / before use"))
    "适合", "选择", "支持", "提供", "帮助", "满足", "保持", "避免",
    "提升", "改善", "结合", "根据", "针对", "包括", "主要", "其他",
    "以下", "以上", "部分", "整体", "快速", "有效", "科学", "健康",
    "减肥", "减脂", "瘦身", "燃脂", "塑形", "体重管理", "饮食", "运动",
    "热量", "卡路里", "营养", "免费", "付费", "下载", "安装", "界面",
    "数据", "记录", "打卡", "社区", "课程", "计划", "目标", "专业",
    "简单", "方便", "全面", "丰富", "精准", "智能", "综上所述",
    "总的来说", "值得注意", "核心功能", "使用前", "使用后",
    # Cross-industry generic adjectives/judgment words (measured noise in the home elevator industry 2026-08-10: 适用 12.5% ("applicable 12.5%"))
    "适用", "通用", "常用", "实用", "耐用", "一般", "常见", "典型",
    "普通", "多样", "定制", "标配", "选配", "优选", "主流", "高端",
    "中端", "低端", "国产", "进口", "品类", "厂家", "厂商", "企业",
    # Standalone generic nouns (2-3 chars) that can't be brand names
    "案例", "预算", "成本", "需求", "流程", "资质", "报价", "交付",
    "验收", "维保", "材质", "型号", "参数", "工期", "保修", "安装",
    "施工", "设计", "选购", "选型", "对比", "排名", "费用", "周期",
    # IDC/compute industry generic nouns (measured noise for Acme Cloud 2026-08-19: 网络 ("network") topped the list at 62.5%)
    "网络", "机房", "机柜", "带宽", "算力", "云计算", "服务器", "宽带",
    "数据中心", "智算中心", "运营商", "服务商", "供应商", "第三方",
    "基础设施", "液冷", "散热", "供电", "电力", "托管", "专线", "上云",
}

# Candidates whose names contain these fragments are judged noise outright (compound phrases that can't go into the stopword list as whole words)
_ZH_NOISE_SUBSTRINGS = (
    "因人而异", "怎么样", "哪个好", "排行榜", "如下", "方法", "注意",
    "建议", "效果", "禁忌", "原理", "功能", "使用",
)

# Cross-industry generic attribute/category suffixes -- short Chinese candidates ending in these characters are category words, not brands.
# Hand-written per-industry stopword lists can never keep up (the weight-loss list was complete, then home elevators leaked
# 「钢结构 / 售后服务 / 服务模式 / 玻璃井道」 ("steel structure / after-sales service / service model / glass shaft")); suffixes are an industry-independent structural criterion.
_ZH_CATEGORY_SUFFIXES = (
    "结构", "服务", "模式", "方案", "系统", "工艺", "材料", "成本",
    "价格", "费用", "流程", "标准", "功能", "性能", "维护", "保养",
    "安装", "设计", "尺寸", "空间", "类型", "种类", "参数", "配置",
    "品质", "质量", "售后", "口碑", "优势", "劣势", "趋势", "市场",
    "行业", "需求", "场景", "井道", "规格", "预算", "体验", "保障",
    "周期", "效率", "能力", "水平", "指标", "风险", "问题", "对比",
    "分析", "报告", "数据", "资质", "认证", "培训", "案例",
    # Category words in marketing/communications (the top of the Juxing Media report was 「明星代言」 ("celebrity endorsement"))
    "代言", "投放", "植入", "曝光", "种草", "推广", "获客", "转化",
)
# A suffix alone isn't enough to judge noise -- 「泓动数据」 ("Hongdong Data") also ends with 「数据」 ("data") but is a real company.
# The prefix must also be a generic modifier for it to hold (售后+服务, 地面+材料, 钢+结构 ("after-sales + service, floor + material, steel + structure")).
_ZH_GENERIC_PREFIXES = {
    "售后", "售前", "服务", "适用", "适配", "应用", "使用", "专业", "核心",
    "主要", "基本", "具体", "实际", "整体", "相关", "综合", "常见", "典型",
    "地面", "墙面", "顶面", "安装", "施工", "装修", "价格", "成本", "预算",
    "质量", "品质", "技术", "产品", "品牌", "市场", "行业", "用户", "客户",
    "项目", "申请", "办理", "选择", "对比", "案例", "资质", "材料", "工艺",
    "结构", "空间", "尺寸", "功能", "性能", "维护", "保养", "前期", "后期",
    "日常", "长期", "短期", "国内", "国外", "全国", "本地", "线上", "线下",
    "免费", "收费", "高端", "中端", "低端", "定制", "标准", "通用", "智能",
    "环保", "安全", "舒适", "美观", "实用", "砖混", "钢混", "框架", "装配",
    "玻璃", "不锈", "实木", "瓷砖", "金属", "塑料", "复合", "液压", "曳引",
    "运营", "运维", "交付", "验收", "报价", "配送", "物流", "支付", "结算",
    # Single-character prefixes (three-character candidates: 钢结构 / 木结构 / 高成本 ("steel structure / wood structure / high cost"))
    "钢", "木", "铁", "铝", "玻", "砖", "混", "石", "高", "低", "大", "小",
    "新", "旧", "全", "半", "多", "单", "双", "总", "主", "次", "长", "短",
    # Generic modifiers in marketing/communications (明星+代言, 内容+种草, 效果+投放 ("celebrity + endorsement, content + seeding, performance + placement"))
    "明星", "艺人", "网红", "达人", "内容", "整合", "效果", "口碑",
    "私域", "流量", "社交", "直播", "短视", "全域", "跨界", "联名",
}
# The suffix criterion only applies to candidates that are "pure Chinese and at most 4 characters" -- real brand names usually carry a company suffix
# (e.g. "Kangli Elevator Co., Ltd.") or contain Latin characters, so they aren't falsely killed by this rule
_ZH_CATEGORY_MAX_LEN = 4

_ZH_ONLY_RE = re.compile(r'^[一-鿿]+$')


def _zh_noisy(name: str) -> bool:
    if any(s in name for s in _ZH_NOISE_SUBSTRINGS):
        return True
    if (
        3 <= len(name) <= _ZH_CATEGORY_MAX_LEN
        and _ZH_ONLY_RE.match(name)
        and name.endswith(_ZH_CATEGORY_SUFFIXES)
        and name[:-2] in _ZH_GENERIC_PREFIXES
    ):
        return True
    return False


def is_noise_brand_name(name: str) -> bool:
    """Whether the name itself is noise (category word / section heading / generic word).

    Public for historical data cleanup scripts -- persisted reports can't recover the raw answers or rerun LLM
    tagging, so they can only be re-screened with this structural criterion that shares a source with production."""
    n = (name or "").strip()
    if len(n) < 2:
        return True
    if n in _ZH_STOPWORDS or _zh_noisy(n):
        return True
    if n.lower() in _COMMON_WORDS:
        return True
    return False


_NAME_NORM_RE = re.compile(r'[\s·・\-_/()（）「」【】""\'\']+')


def _norm_name(name: str) -> str:
    return _NAME_NORM_RE.sub("", (name or "").strip().lower())


def _make_allow_gate(allowed_names: Optional[List[str]]):
    """Competitor names tagged by the LLM -> allowlist gate.

    The same sample batch has already gone through one LLM batch tagging (the prompt says explicitly "extract only same-category competitors, not platform names /
    media names / generic words / category words"), and that list is much cleaner than regex. When it exists, regex candidates only provide
    "surface form + mention count", and the allowlist decides whether they make the ranking; without it the gate lets everything through, falling back to
    stopwords + suffix fallback.

    Matching is relaxed to mutual containment (e.g. "Mitsubishi" <-> "Mitsubishi Elevator", "Kangli Elevator" <-> "Kangli Elevator Co.,
    Ltd."), otherwise the same company would be judged as two competitors because of short vs long names."""
    norms = {n for n in (_norm_name(x) for x in (allowed_names or [])) if len(n) >= 2}
    if not norms:
        return None

    def _allowed(name: str) -> bool:
        n = _norm_name(name)
        if len(n) < 2:
            return False
        # The allowlist isn't an inspection-free pass -- generic words still have to clear the noise gate.
        # (Measured on Acme Cloud 2026-08-19: 「网络」 ("network") rode a substring match onto some allowlisted company name containing 「网络」
        #  and took the top spot, 40 mentions at 62.5%, pushing GDS Holdings to second.)
        if is_noise_brand_name(name):
            return False
        if len(n) < 4:
            # 2-3 character candidates only accept exact matches: substring matching would let generic words like 「网络」 ("network") / 「数据」 ("data")
            # ride any longer allowlist entry into the ranking
            return any(n == a for a in norms)
        return any(n == a or n in a or a in n for a in norms)

    return _allowed


def _dedupe_allowed(allowed_names: Optional[List[str]]) -> List[str]:
    """Dedupe within the LLM list: keep only the longest of the same company's short and long names."""
    seen: Dict[str, str] = {}
    for raw in allowed_names or []:
        name = (raw or "").strip()
        n = _norm_name(name)
        if len(n) < 2:
            continue
        seen.setdefault(n, name)
    kept: List[str] = []
    for n, name in sorted(seen.items(), key=lambda kv: -len(kv[0])):
        if any(n in _norm_name(k) for k in kept):
            continue
        kept.append(name)
    return kept

# Common English words to exclude from brand detection
_COMMON_WORDS = {
    "the", "and", "for", "with", "from", "that", "this", "your", "they",
    "are", "but", "not", "you", "all", "can", "had", "her", "was", "one",
    "our", "out", "has", "its", "let", "may", "who", "did", "get", "top",
    "best", "most", "also", "more", "very", "here", "just", "like", "make",
    "over", "such", "than", "them", "then", "what", "when", "will", "each",
    "key", "new", "now", "how", "why", "two", "way", "use", "see", "first",
    "some", "time", "been", "long", "note", "well", "back", "much", "take",
    # "keep" is not in this list -- the fitness brand Keep is a real competitor and was falsely killed before (2026-07-30)
    "only", "come", "both", "many", "good", "year", "last", "same",
    "work", "need", "help", "high", "line", "turn", "move", "live", "real",
    "left", "team", "since", "every", "right", "large", "based", "after",
    "these", "those", "other", "their", "which", "about", "would", "there",
    "being", "great", "through", "still", "where", "should", "while", "world",
    "could", "below", "above", "today", "known", "using", "offer", "read",
    "built", "free", "full", "plan", "look", "find", "here", "type", "point",
    # Common sentence starters / section headers
    "overview", "features", "pricing", "summary", "conclusion", "introduction",
    "comparison", "alternative", "alternatives", "category", "solution",
    "solutions", "platform", "service", "services", "product", "products",
    "tool", "tools", "review", "reviews", "pros", "cons", "key",
    "however", "additionally", "furthermore", "moreover", "therefore",
    "overall", "specifically", "particularly", "essentially", "generally",
    "merchant", "record", "processing", "payment", "payments", "gateway",
    "integration", "transaction", "transactions", "business", "businesses",
    "global", "digital", "online", "credit", "debit", "card",
    # Common false positives from AI answers
    "agent", "base", "note", "step", "visit", "sign", "check", "data",
    "user", "code", "test", "link", "home", "page", "site", "blog",
    "post", "form", "view", "list", "item", "file", "docs", "head",
    "open", "next", "main", "core", "edge", "node", "true", "false",
    "null", "void", "scam", "spam", "fake", "risk", "safe", "chat",
    # Crypto terms (not brands)
    "usdc", "usdt", "eth", "btc", "sol", "bnb", "defi", "nft",
    "blockchain", "token", "tokens", "crypto", "chain", "web3",
    "solana", "polygon", "ethereum", "bitcoin", "arbitrum",
    # Generic tech terms
    "api", "sdk", "saas", "rest", "http", "json", "html", "css",
    "ios", "android", "linux", "windows", "macos", "docker", "cloud",
    # Single words that are usually not brand names
    "smart", "fast", "easy", "simple", "basic", "standard", "premium",
    "enterprise", "professional", "advanced", "custom", "modern",
    "universal", "protocol", "framework", "library", "module",
    # More false positives
    "success", "error", "result", "status", "response", "request",
    "github", "gitlab", "npm", "pip", "maven", "gradle",
    "gas", "gasless", "gasle", "fee", "fees", "cost", "costs",
    "low", "medium", "large", "small", "tiny", "huge",
    "setup", "config", "enable", "disable", "start", "stop",
    "input", "output", "source", "target", "debug", "log",
    "app", "web", "mobile", "desktop", "server", "client",
    "pro", "plus", "max", "mini", "lite", "basic",
    "beta", "alpha", "dev", "prod", "staging",
    # AI/ML terms
    "agent", "agents", "autonomous", "model", "models", "neural",
    "training", "inference", "prompt", "prompts", "embedding",
    # Generic verbs/adjectives appearing as capitalized sentence starters
    "search", "approval", "important", "available", "required",
    "supported", "included", "designed", "created", "built",
    "allows", "provides", "offers", "enables", "supports",
    "according", "depending", "including", "regarding", "following",
    # More noise
    "molt", "multi", "wallet", "march", "tempo", "fiat", "onramps",
    "strengths", "weaknesses", "reliable", "trustworthy",
    "chain", "smart", "contract", "contracts", "layer",
    "pre", "post", "sub", "non",
    # Multi-word noise patterns
    "simple integration", "gasless transactions", "easy setup",
    "fast processing", "low fees", "high performance",
    "real time", "open source", "cross border", "end to end",
}

# Framing classification patterns
_FRAMING_PATTERNS = {
    "recommended": [
        "recommend", "use", "consider", "try", "choose", "go with",
        "is the best", "is a leading", "is a top", "stands out",
        "ideal for", "great for", "perfect for", "excellent",
    ],
    "leader": [
        "is a leader", "is one of the leading", "is a major", "is a popular",
        "is well-known", "is widely used", "is a prominent", "market leader",
        "dominant", "largest",
    ],
    "option": [
        "is an option", "is one option", "is an alternative", "is another",
        "can also", "you could", "worth considering",
    ],
    "niche": [
        "is a niche", "is experimental", "is a newer", "is a small",
        "is relatively new", "is emerging", "lesser-known",
    ],
}


# Chinese sentiment words -- Chinese has no spaces, so judge by "a sentiment word within +/-40 chars of the brand name"
_ZH_FRAMING_PATTERNS = {
    "recommended": ["推荐", "首选", "值得一试", "不错的选择", "建议使用", "优先考虑"],
    "leader": ["领先", "头部", "知名", "主流", "龙头", "排名第一", "市场领导"],
    "option": ["也可以", "另一个选择", "备选", "可以考虑", "替代方案"],
    "niche": ["小众", "新兴", "冷门", "较新"],
}


_LATIN_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9 .\-]*$')


def _brand_in_answer(answer_lower: str, brand_lower: str) -> bool:
    """Whether the brand appears in the answer. Short pure-Latin names use whole-word matching -- substring matching made
    "pp" hit "<brand-b> APP" and "eal" hit "Smeal" (2026-07-30 incident: Pp mention rate 100%)."""
    if _LATIN_NAME_RE.match(brand_lower):
        return re.search(rf'(?<![a-z0-9]){re.escape(brand_lower)}(?![a-z0-9])',
                         answer_lower) is not None
    return brand_lower in answer_lower


def _classify_framing(answer: str, brand: str) -> str:
    """Classify how an AI answer frames a brand."""
    answer_lower = answer.lower()
    brand_lower = brand.lower()
    if brand_lower not in answer_lower:
        return "not_mentioned"
    for framing, patterns in _FRAMING_PATTERNS.items():
        for pat in patterns:
            if f"{brand_lower} {pat}" in answer_lower or f"{pat} {brand_lower}" in answer_lower:
                return framing
    # Chinese: look for sentiment words in a +/-40 char window around each occurrence of the brand
    positions = [m.start() for m in re.finditer(re.escape(brand_lower), answer_lower)]
    for pos in positions[:10]:
        window = answer_lower[max(0, pos - 40): pos + len(brand_lower) + 40]
        for framing, patterns in _ZH_FRAMING_PATTERNS.items():
            for pat in patterns:
                if pat in window:
                    return framing
    return "mentioned"


def _extract_brands_from_text(results, target_brand: str) -> Counter:
    """Extract brand names from answer text — the PRIMARY signal.

    Looks for capitalized multi-word names (e.g., "Shopify Payments") and
    single capitalized words that aren't common English.
    """
    brand_mentions = Counter()
    target_lower = target_brand.lower()

    for r in results:
        if r.error or not r.answer:
            continue

        # Strategy 1 (HIGH confidence): Extract bold/emphasized terms
        # AI engines typically bold brand names: **Stripe**, **PayPal**
        bold_names = re.findall(r'\*\*([A-Z][A-Za-z0-9]+(?:\s+[A-Za-z0-9]+){0,2})\*\*', r.answer)
        for name in bold_names:
            nl = name.lower()
            if nl != target_lower and nl not in _COMMON_WORDS and len(name) > 2:
                brand_mentions[name] += 2  # double weight for bold names

        # Strategy 2 (HIGH confidence): Extract from numbered/bulleted lists
        # AI engines list competitors as "1. **Stripe** — ..." or "- PayPal: ..."
        list_items = re.findall(
            r'(?:^|\n)\s*(?:\d+\.\s*|\-\s*|\*\s*)(?:\*\*)?([A-Z][A-Za-z0-9]+(?:\s+[A-Za-z0-9]+){0,2})(?:\*\*)?',
            r.answer,
        )
        for name in list_items:
            nl = name.lower()
            if nl != target_lower and nl not in _COMMON_WORDS and len(name) > 3:
                brand_mentions[name] += 2

        # Strategy 3 (MEDIUM confidence): Extract from "alternatives include X, Y, Z"
        list_pattern = re.findall(
            r'(?:include|such as|like|alternatives to)[:\s]+([^.]+)',
            r.answer, re.IGNORECASE,
        )
        for match in list_pattern:
            items = re.split(r'[,;]|\band\b', match)
            for item in items:
                item = item.strip().strip('*').strip()
                if (
                    item and item[0].isupper() and len(item) > 3
                    and item.lower() != target_lower
                    and item.lower() not in _COMMON_WORDS
                ):
                    brand_mentions[item] += 1

        # Strategy 4 (HIGH confidence): Chinese brands -- in CN engines' answers competitors have
        # Chinese names (e.g. Boohee / Herbalife), which the three Latin regexes above can't catch at all.
        # Both bold and list forms, filtered by _ZH_STOPWORDS (section headings / category words).
        zh_bold = re.findall(r'\*\*([一-鿿][一-鿿A-Za-z0-9]{1,9})\*\*', r.answer)
        zh_list = re.findall(
            r'(?:^|\n)\s*(?:\d+\.\s*|[-*]\s*)(?:\*\*)?([一-鿿][一-鿿A-Za-z0-9]{1,9})(?:\*\*|[::\s]|$)',
            r.answer,
        )
        for name, weight in [(n, 2) for n in zh_bold] + [(n, 2) for n in zh_list]:
            name = name.strip()
            nl = name.lower()
            if (
                len(name) >= 2
                and nl != target_lower
                and target_lower not in nl          # "<brand-b> APP" isn't a competitor
                and name not in _ZH_STOPWORDS
                and not _zh_noisy(name)
                and nl not in _COMMON_WORDS
            ):
                brand_mentions[name] += weight

    return brand_mentions


def _extract_competitor_domains(results, target_domain: str) -> Counter:
    """Extract competitor domains from citation URLs (secondary signal)."""
    competitors = Counter()
    for r in results:
        if r.error:
            continue
        for c in r.citations:
            d = c.domain.lower()
            if (
                d and d != target_domain and "." in d
                and d not in _PLATFORM_DOMAINS
                and not any(d.endswith("." + p) for p in _PLATFORM_DOMAINS)
            ):
                competitors[d] += 1
    return competitors


def analyze_competitor_insight(
    results,
    target_domain: str = "",
    target_brand: str = "",
    competitor_domains: Optional[List[str]] = None,
    allowed_names: Optional[List[str]] = None,
    rate_scope: Optional[Any] = None,
) -> Dict[str, Any]:
    """Analyze how competitors appear in AI engine responses.

    Uses BOTH text-level brand extraction (primary) and citation URL
    extraction (secondary) to build the competitor landscape.

    allowed_names: competitor list tagged by the LLM. If given it acts as an allowlist -- regex-extracted candidates
    must match the list to enter the ranking, and names on the list the regex missed are added too. If not given fall back to stopwords.

    rate_scope: the metric definition for mention rate, `fn(result) -> bool`; only responses judged True enter
    numerator and denominator. **Must** exclude brand-word/comparison-word questions -- a question like "how is Zhuyou Shengxinzhuang"
    already contains the brand name, so the model inevitably mentions it; counting it hands our brand free mention rate (measured: our brand's
    21.4% came entirely from these 3 giveaway questions; on the 11 industry-word questions it was actually 0/44), while competitors look falsely low
    because the denominator is inflated, so the ranking's conclusion is exactly reversed. The definition matches the report center exposure rate
    (EXPOSURE_ONLY_SCENES = brand + compare).
    """
    all_valid = [r for r in results if not r.error]
    valid_queries = [r for r in all_valid if rate_scope(r)] if rate_scope else all_valid
    scoped = bool(rate_scope) and len(valid_queries) != len(all_valid)
    if not valid_queries:
        # The user only filled in brand words -> the category scope is empty; fall back to everything (rate_scope_note explains it)
        valid_queries, scoped = all_valid, False
    total_valid = len(valid_queries) or 1

    allowed_names = _dedupe_allowed(allowed_names)
    allow_gate = _make_allow_gate(allowed_names)

    # ── Primary: extract brands from answer text ─────────
    # Name extraction uses all responses (comparison questions also name competitors); only mention rate narrows to the category scope
    text_brands = _extract_brands_from_text(all_valid, target_brand)

    # ── Secondary: extract from citation URLs ────────────
    url_competitors = _extract_competitor_domains(results, target_domain)

    # ── Merge: combine text brands + URL competitors ─────
    # Normalize: for URL competitors, create a brand name from domain
    all_brands: Dict[str, Dict] = {}

    # Add text-extracted brands (filter to those mentioned at least twice)
    for name, count in text_brands.most_common(50):
        if count >= 3:  # noise filter — need 3+ weighted mentions
            if allow_gate and not allow_gate(name):
                continue
            key = name.lower()
            all_brands[key] = {
                "name": name,
                "text_mentions": count,
                "citation_count": 0,
            }

    # Add names from the LLM list the regex missed (narrative answers without bold or lists can't be caught by regex anyway);
    # real mention counts are scanned across the full text below; added names that never appear get filtered by mention_count
    def _is_target(nm: str) -> bool:
        """Whether this name is our own brand.

        ★ Added 2026-09-28: the regex extraction path always had `nl != target_lower`, but the LLM allowlist path didn't --
          the model occasionally submits our own brand as a competitor, so the ranking showed the same brand twice
          (report #146: "<brand-i> (own brand) 46.0%" followed by "<brand-i> 52.0% high threat").
          After normalization, mutual containment counts as the same, so "<brand-i>" and "<brand-i> cooking oil" aren't treated as two companies.
        """
        if not target_brand or not nm:
            return False
        a, b = _norm_name(nm), _norm_name(target_brand)
        return bool(a) and bool(b) and (a in b or b in a)

    for name in allowed_names:
        # The list isn't inspection-free: the model occasionally submits category words as competitors (the top of the Juxing Media report was
        # 「明星代言」 ("celebrity endorsement") with 13 mentions at 31%, above every real company). Regex candidates go through
        # the same gate in allow_gate; it's applied here too so the allowlist doesn't become a green lane for noise.
        if is_noise_brand_name(name) or _is_target(name):
            continue
        key = name.lower()
        if key not in all_brands and not any(
            _norm_name(name) in _norm_name(v["name"]) or _norm_name(v["name"]) in _norm_name(name)
            for v in all_brands.values()
        ):
            all_brands[key] = {"name": name, "text_mentions": 0, "citation_count": 0}

    # Add/merge URL competitors -- use the registered domain main label and pass the noise gate
    # (the old logic split(".")[0] treated the m in m.sohu.com and the news in news.xx as brands)
    target_label = _registrable_label(target_domain) if target_domain else ""
    for domain, cite_count in url_competitors.most_common(20):
        label = _registrable_label(domain)
        if (
            not label
            or len(label) < 3   # short labels like pp/m are almost all subdomains/abbreviations, not brands
            or label in _GENERIC_DOMAIN_LABELS
            or label in _COMMON_WORDS
            or label == target_label
        ):
            continue
        # With an LLM list, domain labels must pass the gate too -- cited sites are mostly media/news sites,
        # and using the domain main label as a brand is another noise source for the competitor ranking
        if allow_gate and not allow_gate(label):
            continue
        brand_name = label.capitalize()
        key = brand_name.lower()
        if key in all_brands:
            all_brands[key]["citation_count"] += cite_count
        else:
            all_brands[key] = {
                "name": brand_name,
                "text_mentions": 0,
                "citation_count": cite_count,
            }

    # Add user-specified competitors if not already found
    # Competitors named by the user **must all stay**: even with zero mentions they remain on the ranking --
    # "the rival you named has 0 exposure in AI answers" is itself a conclusion this report must deliver;
    # if the mention_count > 0 gate filtered them out, the customer would see a ranking of names they don't recognize.
    user_named = set()
    if competitor_domains:
        for cd in competitor_domains:
            raw = str(cd or "").strip()
            if not raw:
                continue
            # Chinese names kept as-is; only domain forms (brand-b.example) take the main label
            brand_name = raw.split(".")[0].capitalize() if "." in raw else raw
            key = brand_name.lower()
            user_named.add(key)
            if key not in all_brands:
                all_brands[key] = {
                    "name": brand_name,
                    "text_mentions": 0,
                    "citation_count": 0,
                }

    # ── Per-competitor deep analysis ─────────────────────
    competitors = []
    for key, info in all_brands.items():
        brand_name = info["name"]
        brand_lower = brand_name.lower()
        # Exit fallback: our own brand never enters the competitor ranking, whichever path it sneaked into all_brands
        if _is_target(brand_name):
            continue

        # Count unique query mentions + per-engine tracking
        mention_count = 0
        framings = Counter()
        engines_seen = set()

        for r in valid_queries:
            answer_lower = r.answer.lower()
            if _brand_in_answer(answer_lower, brand_lower):
                mention_count += 1
                engines_seen.add(r.engine)
                framing = _classify_framing(r.answer, brand_name)
                framings[framing] += 1

        rate = round(mention_count / total_valid * 100, 1)
        primary_framing = framings.most_common(1)[0][0] if framings else "not_mentioned"

        # Only include if actually mentioned in at least 1 answer (except those named by the user)
        is_user_named = key in user_named
        if mention_count > 0 or info["citation_count"] > 0 or is_user_named:
            competitors.append({
                "domain": f"{brand_lower}.com",
                "name": brand_name,
                "citation_count": info["citation_count"],
                "mention_count": mention_count,
                "rate": rate,
                "framing": primary_framing,
                "framings": dict(framings),
                "engines": sorted(engines_seen),
                "user_specified": is_user_named,
            })

    # Sort by mention count (primary) + citation count (secondary)
    competitors.sort(key=lambda c: -(c["mention_count"] * 10 + c["citation_count"]))
    # Pick out user-named ones before truncating -- zero-mention ones are sorted last, and a plain [:20] would cut them off
    named = [c for c in competitors if c.get("user_specified")]
    rest = [c for c in competitors if not c.get("user_specified")]
    competitors = (named + rest)[:20] if len(named) >= 20 else \
        sorted(named + rest[:max(0, 20 - len(named))],
               key=lambda c: -(c["mention_count"] * 10 + c["citation_count"]))

    # ── Your brand analysis ──────────────────────────────
    top_competitor = competitors[0] if competitors else None
    your_mention_count = 0
    your_framings = Counter()
    for r in valid_queries:
        if target_brand and (
            _brand_in_answer(r.answer.lower(), target_brand.lower())
            or (target_domain and target_domain in r.answer.lower())
        ):
            your_mention_count += 1
            framing = _classify_framing(r.answer, target_brand)
            your_framings[framing] += 1

    your_rate = round(your_mention_count / total_valid * 100, 1) if target_brand else 0

    return {
        "competitors": competitors,
        "total_competitors": len(competitors),
        # Denominator metric definition of mention rate -- the frontend must label it accordingly, otherwise "21.4% vs 7.1%" reads as a lead
        "rate_scope": "category" if scoped else "all",
        "rate_scope_valid": len(valid_queries),
        "rate_scope_total": len(all_valid),
        "top_competitor": top_competitor["name"] if top_competitor else None,
        "top_competitor_rate": top_competitor["rate"] if top_competitor else 0,
        "top_competitor_mentions": top_competitor["mention_count"] if top_competitor else 0,
        "your_rate": your_rate,
        "your_framing": your_framings.most_common(1)[0][0] if your_framings else "not_mentioned",
        "monitored_keywords": 1,
    }
