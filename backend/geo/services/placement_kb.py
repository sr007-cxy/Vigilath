"""Placement strategy knowledge base -- which sources each of the five major AI engines trusts, and which content forms they want.

This is the **static half** of the plan's "placement strategy" page (the dynamic half is the topic's measured citation data,
see services/placement_strategy.py). Source of the metric definition: `docs/03-系统设计/内容与发布/GEO媒体投放策略生成器.md`
-- when changing rules here, change that doc too; the two must not contradict each other.

## Why a separate module

1. **Pure constants, zero IO**: strategy generation must be reproducible -- the same measured data + the same knowledge base version
   always produce the same strategy. With any IO / LLM / randomness, "the plan changes only when the strategy changes" no longer holds.
2. **Version traceable**: `KB_VERSION` is persisted along with the strategy snapshot. If rules here change later, old plans'
   strategy snapshots still make sense (same idea as the thresholds snapshot in source_plan).

## Engine ids

Consistent with telemetry (models/ai_telemetry.py::VALID_ENGINES):
deepseek / doubao / qwen / wenxin / yuanbao. Engines not in this table (overseas engines, Kimi...)
use `GENERIC_PROFILE`, and the strategy states plainly "no dedicated metric definition", not pretending to know.

## Platform name discipline

Platform names in `platforms` / `checklist[].platform` must be **character-for-character identical** to the Chinese keys of
`services/source_plan.py::PLATFORM_DOMAINS` (plus the two special buckets "官网博客" ("website blog")
and "软文匠" ("Ruanwenjiang")). A single wrong character means the platform recommended by the strategy cannot feed the quota or enter the publish schedule.
"""
from __future__ import annotations

from typing import Any, Optional

# Knowledge base version -- +1 whenever any rule below changes (strategy snapshots are interpreted by it)
KB_VERSION = "placement_kb_v1"

# Three ways a placement checklist item lands:
#   publish -- content placement that can go into the publish schedule (must carry platform)
#   profile -- cannot be scheduled, needs a person to create/edit profiles (encyclopedia entries, company info, search indexing)
#   account -- one-time account-side setup (verification, webmaster verification)
# The latter two go to the optimization task center and take no publish schedule slots.
KIND_PUBLISH = "publish"
KIND_PROFILE = "profile"
KIND_ACCOUNT = "account"


ENGINE_PROFILES: dict[str, dict[str, Any]] = {
    "doubao": {
        "name": "豆包",
        "ecosystem": "字节生态",
        "summary": "生态利益优先:字节系内容(抖音 / 头条 / 抖音百科)是绝对主力,"
                   "手机端更偏短视频,网页端才回到权威图文。",
        "tiers": [
            {"tier": "T0", "label": "顶级权威",
             "types": "政府监管官网、国家级央媒、企业官方官网、平台认证账号、抖音百科词条",
             "rule": "可单独支撑结论"},
            {"tier": "T1", "label": "高权威",
             "types": "垂类头部媒体、全国性行业协会、持证检测机构、头条原创深度资讯、认证行业作者",
             "rule": "必须与另一条高权威信源交叉印证"},
            {"tier": "T2", "label": "普通补充",
             "types": "普通问答社区回答、百家号干货、社区平台专业实测长文、中小行业资讯站",
             "rule": "只作辅助叙事,不支撑核心数据"},
            {"tier": "T3", "label": "淘汰级",
             "types": "无备案个人站、企业黄页、批量洗稿、付费推广落地页、三年以上旧资讯",
             "rule": "全域拦截"},
        ],
        "exclusive": "抖音百科(其它引擎抓不到的独家权威词条)",
        "content_forms": ["问答式结构", "对比清单表", "实景短视频(手机端)"],
        "content_avoid": "散文式论述、关键词堆砌、绝对化营销话术",
        "recency": "近两周内发布的内容权重最高,带明确时间标注的更容易被引",
        "priority_list": [
            "抖音百科官方认证词条",
            "企业认证账号 + 官网",
            "今日头条原创深度资讯",
            "垂类头部媒体与认证作者",
            "问答社区专业回答 / 百家号干货",
        ],
        "checklist": [
            {"key": "douyin_baike", "label": "抖音百科词条创建 / 优化",
             "tier": "T0", "kind": KIND_PROFILE,
             "note": "唯一能单条支撑结论的信源,优先级最高"},
            {"key": "douyin_verified", "label": "抖音企业认证账号建设",
             "tier": "T0", "kind": KIND_ACCOUNT},
            {"key": "biz_registry", "label": "企业信息平台资料补全(工商简介 / 标签)",
             "tier": "T0", "kind": KIND_PROFILE,
             "note": "这类事实站被引频次高,改资料零成本"},
            {"key": "douyin_video", "label": "抖音实景短视频",
             "tier": "T1", "kind": KIND_PUBLISH, "platform": "抖音"},
            {"key": "toutiao_deep", "label": "今日头条原创深度文章",
             "tier": "T1", "kind": KIND_PUBLISH, "platform": "头条号"},
            {"key": "site_faq", "label": "官网问答页与结构化数据",
             "tier": "T0", "kind": KIND_PUBLISH, "platform": "官网博客"},
            {"key": "vertical_media", "label": "垂类头部媒体合作稿",
             "tier": "T1", "kind": KIND_PUBLISH, "platform": "软文匠"},
            {"key": "zhihu_answer", "label": "问答社区专业回答",
             "tier": "T2", "kind": KIND_PUBLISH, "platform": "知乎"},
        ],
        "platforms": ("抖音", "头条号", "官网博客", "知乎", "软文匠", "百家号"),
    },
    "deepseek": {
        "name": "DeepSeek",
        "ecosystem": "开放全网",
        "summary": "技术价值判断优先,没有生态偏袒:精读少数高权威信源做深度解读,"
                   "专业深度不够的内容基本没有曝光。",
        "dimensions": [
            {"name": "技术深度", "weight": 30, "action": "补原理分析、实现细节、性能对比数据"},
            {"name": "逻辑完整性", "weight": 25, "action": "按「问题 → 分析 → 验证 → 结论」组织"},
            {"name": "来源可信度", "weight": 20, "action": "标注作者机构,借专业社区背书"},
            {"name": "信息准确性", "weight": 15, "action": "数据可验证、结论可复现"},
            {"name": "表达可读性", "weight": 10, "action": "分级标题、表格、代码与图示"},
        ],
        "exclusive": "无(开放生态,谁写得深就引谁)",
        "content_forms": ["结构化技术文档", "带可运行示例的实践文", "对比表格", "行业白皮书"],
        "content_avoid": "泛泛而谈的概述文、伪原创、无数据支撑的观点",
        "recency": "对老内容友好 —— 原理与架构类长期有效,版本相关内容需定期更新",
        "priority_list": [
            "技术社区深度长文",
            "开源项目文档与说明",
            "问答社区专业回答(带数据)",
            "官网技术 / 产品文档",
            "行业研究报告与白皮书",
        ],
        "checklist": [
            {"key": "tech_docs", "label": "官网技术 / 产品文档结构化",
             "tier": "T0", "kind": KIND_PUBLISH, "platform": "官网博客"},
            {"key": "tech_community", "label": "技术社区深度长文",
             "tier": "T1", "kind": KIND_PUBLISH, "platform": "软文匠"},
            {"key": "zhihu_pro", "label": "问答社区专业回答(带实测数据)",
             "tier": "T1", "kind": KIND_PUBLISH, "platform": "知乎"},
            {"key": "whitepaper", "label": "行业研究报告 / 白皮书",
             "tier": "T1", "kind": KIND_PUBLISH, "platform": "官网博客"},
            {"key": "oss_profile", "label": "开源项目说明与中文文档完善",
             "tier": "T1", "kind": KIND_PROFILE,
             "note": "有开源资产才做;引用知名项目会带来额外权重"},
        ],
        "platforms": ("官网博客", "知乎", "软文匠", "公众号"),
    },
    "wenxin": {
        "name": "文心一言",
        "ecosystem": "百度生态",
        "summary": "信源偏好与百度搜索排名逻辑高度一致,百科与自有生态内容占比最高,"
                   "对经验 / 专业 / 权威 / 可信这套评估最看重。",
        "exclusive": "百度百科词条",
        "content_forms": ["百科式定义与解释", "问答式结构", "榜单 / 盘点"],
        "content_avoid": "无来源的主观论断、未被搜索收录的孤立页面",
        "recency": "中等 —— 收录与排名比发布时间更决定能否被引",
        "priority_list": [
            "百度百科词条",
            "百家号原创深度内容",
            "搜索排名靠前的官网页面",
            "问答 / 经验类内容",
            "权威媒体新闻源",
        ],
        "checklist": [
            {"key": "baidu_baike", "label": "百度百科词条创建 / 优化",
             "tier": "T0", "kind": KIND_PROFILE},
            {"key": "baijiahao", "label": "百家号原创深度内容",
             "tier": "T1", "kind": KIND_PUBLISH, "platform": "百家号"},
            {"key": "site_index", "label": "官网页面收录与排名优化",
             "tier": "T0", "kind": KIND_PUBLISH, "platform": "官网博客"},
            {"key": "baidu_qa", "label": "问答 / 经验类内容补位",
             "tier": "T2", "kind": KIND_PROFILE},
            {"key": "news_source", "label": "权威媒体新闻源报道",
             "tier": "T1", "kind": KIND_PUBLISH, "platform": "软文匠"},
        ],
        "platforms": ("百家号", "官网博客", "软文匠", "知乎"),
    },
    "qwen": {
        "name": "通义千问",
        "ecosystem": "阿里生态",
        "summary": "看重权威背书与可核验的结构化事实,走交易数据化路线,"
                   "门户媒体报道占比最高,社交平台碎片内容权重低。",
        "exclusive": "电商侧的真实交易与评价数据",
        "content_forms": ["权威媒体报道", "结构化产品参数", "机构号专业内容"],
        "content_avoid": "无出处的自述式宣传、碎片化社交内容",
        "recency": "中等 —— 权威度与可核验性优先于新鲜度",
        "priority_list": [
            "官网(需完成站长平台验证)+ 权威背书",
            "门户 / 权威媒体报道",
            "结构化产品参数与真实评价",
            "问答社区机构号内容",
            "视频社区品牌内容",
        ],
        "checklist": [
            {"key": "webmaster_verify", "label": "阿里站长平台站点验证",
             "tier": "T0", "kind": KIND_ACCOUNT,
             "note": "没验证过的站点很难进入它的可信信源池"},
            {"key": "site_authority", "label": "官网权威内容(资质 / 案例 / 参数)",
             "tier": "T0", "kind": KIND_PUBLISH, "platform": "官网博客"},
            {"key": "portal_media", "label": "门户 / 权威媒体报道",
             "tier": "T1", "kind": KIND_PUBLISH, "platform": "软文匠"},
            {"key": "commerce_data", "label": "商品页结构化数据与真实评价",
             "tier": "T1", "kind": KIND_PROFILE, "note": "有电商业务才做"},
            {"key": "zhihu_org", "label": "问答社区机构号专业内容",
             "tier": "T2", "kind": KIND_PUBLISH, "platform": "知乎"},
            {"key": "bilibili", "label": "视频社区品牌内容",
             "tier": "T2", "kind": KIND_PUBLISH, "platform": "B站"},
        ],
        "platforms": ("官网博客", "软文匠", "知乎", "B站"),
    },
    "yuanbao": {
        "name": "腾讯元宝",
        "ecosystem": "腾讯生态",
        "summary": "生态嵌套:不同场景调不同信源,微信生态内容是其它引擎抓不到的独家信源,"
                   "也是这个引擎最有效的抓手。",
        "exclusive": "微信公众号内容(其它引擎基本抓不到)",
        "content_forms": ["公众号深度长文", "视频号内容", "新闻报道"],
        "content_avoid": "纯外链导流、与微信生态无关联的孤立页面",
        "recency": "中等 —— 生态内优先,时效性不是主要门槛",
        "priority_list": [
            "微信公众号原创深度内容",
            "视频号内容",
            "腾讯新闻 / 企鹅号报道",
            "官网(需被腾讯搜索收录)",
        ],
        "checklist": [
            {"key": "mp_deep", "label": "公众号原创深度内容",
             "tier": "T0", "kind": KIND_PUBLISH, "platform": "公众号",
             "note": "独家信源,优先级最高"},
            {"key": "channels", "label": "视频号内容",
             "tier": "T1", "kind": KIND_PUBLISH, "platform": "视频号"},
            {"key": "tencent_news", "label": "腾讯新闻 / 企鹅号报道",
             "tier": "T1", "kind": KIND_PUBLISH, "platform": "腾讯号"},
            {"key": "tencent_index", "label": "官网被腾讯搜索收录",
             "tier": "T1", "kind": KIND_PROFILE},
        ],
        "platforms": ("公众号", "视频号", "腾讯号", "官网博客"),
    },
}


# Engines not in the table above (overseas engines / Kimi, etc.): do not pretend to have a dedicated metric definition, only give generic rules.
GENERIC_PROFILE: dict[str, Any] = {
    "name": "",
    "ecosystem": "通用",
    "summary": "这个引擎还没有整理专属信源口径,按通用规则处理:"
               "权威可追溯的站点 + 结构清晰的问答式内容。",
    "exclusive": "",
    "content_forms": ["问答式结构", "对比清单表", "权威报道"],
    "content_avoid": "无出处的宣传话术",
    "recency": "未整理",
    "priority_list": ["官网权威内容", "权威媒体报道", "问答社区专业回答"],
    "checklist": [
        {"key": "site_authority", "label": "官网权威内容建设",
         "tier": "T0", "kind": KIND_PUBLISH, "platform": "官网博客"},
        {"key": "zhihu_answer", "label": "问答社区专业回答",
         "tier": "T2", "kind": KIND_PUBLISH, "platform": "知乎"},
    ],
    "platforms": ("官网博客", "知乎", "软文匠"),
    "generic": True,
}


# --- Industry rules ------------------------------------------------------------
# Different industries have very different source structures within the same engine (beauty is almost all short video, enterprise services almost all
# official disclosures). Only two things are done here: give a one-line core strategy, and slightly adjust engine weights per industry.
# The adjustment is deliberately kept within +/-30% -- ranking is still driven by the topic's measured data, not this table.
INDUSTRY_RULES: list[dict[str, Any]] = [
    {
        "key": "beauty",
        "name": "美妆个护 / 母婴",
        "keywords": ["美妆", "护肤", "个护", "母婴", "化妆", "彩妆", "洗护", "日化"],
        "core": "短视频种草主导,抖音生态几乎决定豆包的回答",
        "engine_weight": {"doubao": 1.3, "deepseek": 0.8},
    },
    {
        "key": "digital",
        "name": "3C 数码 / 家电",
        "keywords": ["3c", "数码", "手机", "电脑", "家电", "智能硬件", "耳机", "相机"],
        "core": "实测视频优先,专业评测兜底",
        "engine_weight": {"doubao": 1.2, "deepseek": 1.1},
    },
    {
        "key": "auto",
        "name": "汽车 / 出行",
        "keywords": ["汽车", "出行", "新能源车", "充电", "网约", "车联网", "配件"],
        "core": "试驾 / 用车场景内容为核心,垂类媒体兜底",
        "engine_weight": {"doubao": 1.2},
    },
    {
        "key": "b2b",
        "name": "企业服务 / 工业制造",
        "keywords": ["b2b", "工业", "制造", "企业服务", "设备", "装备", "材料", "化工",
                     "机械", "供应链", "解决方案", "机器人", "saas"],
        "core": "权威公示 + 官方资料是主力,短视频只作辅助",
        "engine_weight": {"qwen": 1.2, "deepseek": 1.2, "doubao": 0.8},
    },
    {
        "key": "medical",
        "name": "医疗健康",
        "keywords": ["医疗", "健康", "医药", "诊疗", "医院", "器械", "生物", "保健"],
        "core": "合规是底线,权威机构与持证专业人士内容优先",
        "engine_weight": {"wenxin": 1.2, "doubao": 0.9},
        "compliance": "医疗强合规:禁功效宣称,需资质与免责声明",
    },
    {
        "key": "fintech",
        "name": "金融 / 财税 / 法律",
        "keywords": ["金融", "财税", "税", "法律", "保险", "投资", "银行", "证券", "基金"],
        "core": "监管口径与持牌背书优先,专业科普辅助",
        "engine_weight": {"qwen": 1.2, "wenxin": 1.1, "doubao": 0.8},
        "compliance": "金融 / 法律强合规:禁收益承诺,需持牌与免责声明",
    },
    {
        "key": "education",
        "name": "教育 / 咨询培训",
        "keywords": ["教育", "培训", "考证", "留学", "课程", "学校", "考试", "咨询"],
        "core": "专业答疑 + 真实体验双驱动",
        "engine_weight": {"doubao": 1.1, "wenxin": 1.1},
    },
    {
        "key": "tech",
        "name": "科技 / 软件 / 开发者",
        "keywords": ["科技", "软件", "开发", "云", "人工智能", "ai", "数据", "算法",
                     "技术", "互联网", "开源", "程序"],
        "core": "技术深度决定一切,专业社区是主战场",
        "engine_weight": {"deepseek": 1.3, "doubao": 0.8},
    },
    {
        "key": "local",
        "name": "本地生活 / 餐饮零售",
        "keywords": ["餐饮", "零售", "本地", "门店", "美食", "商超", "连锁", "生活服务"],
        "core": "真实到店体验内容优先,本地生态权重高",
        "engine_weight": {"doubao": 1.2},
    },
]

GENERIC_INDUSTRY: dict[str, Any] = {
    "key": "general",
    "name": "通用",
    "core": "没有匹配到行业口径,按实测被引分布排序,不做行业加权",
    "engine_weight": {},
    "matched": False,
}


def profile_of(engine: str) -> dict[str, Any]:
    """Engine id -> source profile; engines not curated get the generic profile (name filled with the engine id)."""
    hit = ENGINE_PROFILES.get((engine or "").strip().lower())
    if hit is not None:
        return hit
    return {**GENERIC_PROFILE, "name": (engine or "未知引擎")}


def match_industry(industry: str, extra: str = "") -> dict[str, Any]:
    """Match industry rules by topic.industry text; if no match, return the generic rules (matched=False).

    Matching is the same as source_strategy._industry_prior: keyword containment, first match wins
    (the order of INDUSTRY_RULES is the priority; do not reorder casually).
    """
    hay = f"{industry or ''} {extra or ''}".strip().lower()
    if not hay:
        return dict(GENERIC_INDUSTRY)
    for rule in INDUSTRY_RULES:
        if any(kw in hay for kw in rule["keywords"]):
            return {**rule, "matched": True}
    return dict(GENERIC_INDUSTRY)


def industry_weight(rule: Optional[dict], engine: str) -> float:
    """An industry's weight adjustment for an engine; 1.0 if not configured (no effect on ranking)."""
    if not isinstance(rule, dict):
        return 1.0
    w = (rule.get("engine_weight") or {}).get((engine or "").strip().lower())
    try:
        return float(w) if w else 1.0
    except (TypeError, ValueError):
        return 1.0
