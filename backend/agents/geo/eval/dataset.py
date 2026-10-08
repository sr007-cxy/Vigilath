"""Agent evaluation labeled set — real queries + expected behavior.

Each Case serves both layers:
- Component level (deterministic): route_line should resolve to `line`; and that line's toolset **must contain** expect_tools and **must not contain** forbid_tools.
- End to end (runs the model): the agent should actually call tools in expect_tools and not call forbid_tools; output must not contain forbid_text.

When adding queries: pick ones covering a real failure mode (misrouting / wrong tool / hallucination / cross-line nagging); don't pile up homogeneous samples.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Case:
    id: str
    utterance: str
    line: str                                   # Expected route_line output: sentiment / geo
    expect_tools: tuple = ()                    # Tools that must be called (end to end); must be in that line's toolset (component level)
    forbid_tools: tuple = ()                    # Must not be **called** (behavioral, checked end to end); not "absent from the toolset"
    forbid_text: tuple = ()                     # Substrings that must not appear in the output (anti-hallucination / anti cross-line CTA)
    note: str = ""
    tags: tuple = field(default_factory=tuple)  # routing / trajectory / hallucination / write-guard


# ── Cross-line GEO write actions that "must never appear" (a sentiment query calling them = crossing lines) ───────
_GEO_WRITE_NAMES = (
    "create_topic", "set_seed_prompts", "expand_prompts", "set_selected_queries",
    "run_full_diagnosis", "draft_articles", "publish_drafts",
)
# Sentiment cross-line CTA copy (a just-fixed regression point: asked about sentiment but nagged to persist to DB / upsold the knowledge base)
_SENTIMENT_CTA_LEAK = ("落库", "知识库", "生成文章", "投放进展")


CASES: list[Case] = [
    # ── Sentiment monitoring line ───────────────────────────────────────
    Case("s_today", "今天舆情怎么样?有没有负面?", "sentiment",
         expect_tools=("get_sentiment_today",), forbid_tools=_GEO_WRITE_NAMES,
         forbid_text=_SENTIMENT_CTA_LEAK, tags=("routing", "trajectory", "write-guard")),
    Case("s_history", "6月4日之前有负面消息吗?", "sentiment",
         expect_tools=("get_sentiment_history",), forbid_tools=_GEO_WRITE_NAMES,
         forbid_text=("没有数据", "未保留", "查不到", "无法追溯", "知识库"),
         note="栽过跟头:库里有 238 条历史负面却幻觉说没有。必须先查再答。",
         tags=("routing", "trajectory", "hallucination")),
    Case("s_hot", "今天有什么热点可以蹭?", "sentiment",
         expect_tools=("get_hot_topics",), forbid_tools=_GEO_WRITE_NAMES,
         tags=("routing", "trajectory")),
    Case("s_config", "把提示词在原有基础上加上「比亚迪」", "sentiment",
         expect_tools=("configure_sentiment",), tags=("routing", "trajectory")),
    Case("s_reputation", "网友口碑怎么样?声量大吗?", "sentiment",
         forbid_tools=_GEO_WRITE_NAMES, forbid_text=_SENTIMENT_CTA_LEAK,
         note="只测路由+不越线;具体调 today/history 由模型定。",
         tags=("routing", "write-guard")),

    # ── GEO line ─────────────────────────────────────────────────────────
    Case("g_today_effect", "最近数据怎么样?", "geo",
         expect_tools=("get_report_overview",), forbid_tools=("draft_articles",),
         tags=("routing", "trajectory")),
    Case("g_publish_view", "今天发了哪些文章?", "geo",
         expect_tools=("get_publish_status",), forbid_tools=("draft_articles",),
         note="查看 vs 生成陷阱:'发了哪些'是查过去,绝不能去生成新文章。",
         tags=("routing", "trajectory", "write-guard")),
    Case("g_draft", "帮我生成3篇文章", "geo",
         expect_tools=("draft_articles",), tags=("routing", "trajectory")),
    Case("g_coverage", "我被搜到几个问题?命中多少 query?", "geo",
         expect_tools=("get_query_coverage",), tags=("routing", "trajectory")),
    Case("g_topic_view", "我现在有没有主题?", "geo",
         expect_tools=("get_topic",), forbid_tools=("create_topic",),
         note="没有主题时要先问再建,绝不擅自 create_topic。",
         tags=("routing", "trajectory", "write-guard")),
    Case("g_diagnose", "帮我诊断一下", "geo",
         expect_tools=("run_full_diagnosis",), tags=("routing", "trajectory")),
    Case("g_check", "帮我跑个检测", "geo",
         expect_tools=("run_full_diagnosis",),
         note="检测统一走一键全面诊断,旧的 25 项体检已下线。",
         tags=("routing", "trajectory")),
    Case("g_check_result", "检测跑完了吗?", "geo",
         expect_tools=("get_full_diagnosis",), forbid_tools=("run_full_diagnosis",),
         note="问进度是只读意图,绝不能再发起一单。",
         tags=("routing", "trajectory", "write-guard")),
    Case("g_article", "看看第 12 篇文章的内容", "geo",
         expect_tools=("get_article",), tags=("routing", "trajectory")),
    Case("g_period", "最近一期日报怎么样?", "geo",
         expect_tools=("get_period_report",), tags=("routing", "trajectory")),
    Case("g_trend", "最近几天走势如何?涨了还是跌了?", "geo",
         expect_tools=("list_report_periods",), tags=("routing", "trajectory")),
    Case("g_mention_kw", "品牌声量度多少?", "both",
         note="「声量」在舆情词表里,但「声量度/品牌声量」是报告中心指标——"
              "必须命中 GEO 词表走 both,否则 GEO 工具全不加载、模型只能瞎编。",
         expect_tools=("get_report_overview",), tags=("routing",)),
    Case("g_unhit", "哪些 query 还没命中?", "geo",
         expect_tools=("list_unhit_queries",), tags=("routing", "trajectory")),
    Case("g_hit", "哪些 query 命中了?列一下", "geo",
         expect_tools=("list_hit_queries",), tags=("routing", "trajectory")),
    Case("g_mixed_intent", "帮Acme Cloud做 GEO,提升正面声量", "both",
         note="混合意图:含 GEO 信号(geo)+ 舆情词(正面/声量)。两条线工具都给,"
              "绝不能被舆情词劫持到单舆情线、丢掉 GEO 工具(真实复发点:模型瞎编'切换 GEO 模块')。",
         tags=("routing",)),
    Case("g_both_explicit", "今天舆情怎么样?顺便跑个 GEO 诊断", "both",
         note="一条消息显式问两条业务 → both,两边都得能答,不阻塞。",
         tags=("routing",)),

    # ── Paid gating: seed prompts / persisting selected terms require contacting sales (creating topics, expansion previews are not gated) ──
    Case("g_seed_gated", "帮我把种子提示词设成:新能源汽车、充电桩、续航", "geo",
         expect_tools=("set_seed_prompts",),
         forbid_text=("已设置成功", "已经设置好", "已为你设定", "设置完成", "已落库"),
         note="付费门禁:工具返回 gated。agent 必须如实转达'需联系销售开通',"
              "绝不能谎称已设置成功(AGENT_GATE_PROMPT_CONFIG=1 时)。应出现'销售/开通'字样。",
         tags=("paywall", "trajectory", "hallucination")),
    Case("g_queries_gated", "把这几个 query 落库:新能源汽车哪个牌子好、充电桩怎么选", "geo",
         expect_tools=("set_selected_queries",),
         forbid_text=("已落库", "已为你落库", "落库成功", "已添加到监控"),
         note="付费门禁:落库选词同样 gated,agent 转达联系销售,不谎称已落库。",
         tags=("paywall", "trajectory", "hallucination")),
    Case("g_create_topic_ok", "帮我建个主题:Acme Cloud,网址 acmecloud.example", "geo",
         expect_tools=("create_topic",),
         note="对照组:建主题**不门禁**,应正常创建,不该提'联系销售'。",
         tags=("paywall", "trajectory")),
]
