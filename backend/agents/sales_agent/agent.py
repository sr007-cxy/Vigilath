"""Sales Agent Pydantic AI agent construction."""
from __future__ import annotations

import re

from functools import lru_cache

from agents.sales_agent.deps import SalesAgentDeps
from agents.sales_agent.model import get_sales_agent_model, get_sales_agent_model_settings
from agents.sales_agent.prompts import SALES_AGENT_GUIDE_REPLY, SALES_AGENT_PLAIN_CHAT_PROMPT, SALES_AGENT_SYSTEM_PROMPT
from agents.sales_agent.tools import TOOLS

SYSTEM_PROMPT = SALES_AGENT_SYSTEM_PROMPT


@lru_cache(maxsize=8)
def build_sales_agent(scene: str = "chat"):
    from pydantic_ai import Agent, ModelSettings, RunContext
    from pydantic_ai.capabilities import AbstractCapability

    class RequireFirstToolCall(AbstractCapability):
        """Force a specific tool on the first model step, then allow a normal final answer."""

        def __init__(self, tool_name: str) -> None:
            self.tool_name = tool_name

        def get_model_settings(self):
            def settings(ctx: RunContext) -> ModelSettings:
                for message in getattr(ctx, "messages", []) or []:
                    for part in getattr(message, "parts", []) or []:
                        if getattr(part, "part_kind", "") == "tool-return" and getattr(part, "tool_name", "") == self.tool_name:
                            return ModelSettings()
                return ModelSettings(tool_choice=[self.tool_name])

            return settings

    capabilities = [RequireFirstToolCall("recommend_companies")] if scene in {"lead", "lead_json"} else []
    tools = [] if scene == "plain_chat" else TOOLS
    model_scene = "chat" if scene == "plain_chat" else scene
    system_prompt = SALES_AGENT_PLAIN_CHAT_PROMPT if scene == "plain_chat" else SYSTEM_PROMPT
    return Agent(
        get_sales_agent_model(model_scene),
        deps_type=SalesAgentDeps,
        system_prompt=system_prompt,
        tools=tools,
        retries=2,
        model_settings=get_sales_agent_model_settings(model_scene),
        capabilities=capabilities,
        end_strategy="exhaustive",
    )


_REPORT_STRONG_KW = ("报告", "检测", "诊断", "体检", "分数", "评分", "生成报告", "看报告", "读报告")
_REPORT_URL_KW = ("url", "网址", "链接")
_SERVICE_SCOPE_KW = ("seo", "geo", "官网基建", "官网优化", "ai visibility", "aeo", "llms.txt", "结构化数据")
_ICP_KW = ("画像", "icp", "目标客户", "客群", "行业", "地区", "买家", "服务范围")
# Find customers: **only high-confidence phrases and verb-object/quantity patterns**, not generic nouns like "企业/公司/客户/推荐" ("enterprise/company/customer/recommend").
# Generic nouns have no discriminating power in sales conversations, and _LEAD_KW sits in the fallback position (the last of all word lists),
# meaning "anything unclaimed counts as finding customers" — 「有哪些开信的公司?」 ("which companies opened the email?") was judged
# as lead just because of "公司" ("company"), popping up our own profile card (production, 2026-08-25).
# Adding "开信" ("opened email") to _LOOKUP_KW doesn't fix it: the next user will say "点开的" ("clicked") / "看过的" ("viewed") / "有反应的" ("responded").
# The word lists are now positioned as a **fast path** (a hit saves one model call), not the criterion —
# unrecognized input falls to chat, handed to the classifier in flow/intent.py (see 05-Evaluation §8.4).
_LEAD_KW = (
    "获客", "找客户", "找企业", "获取客户", "获取顾客",
    "客户名单", "顾客名单", "销售线索", "客户线索",
    "推荐客户", "推荐企业", "推荐线索",
    "继续推荐", "再推荐一批", "在推荐一批", "继续找客户", "继续找企业",
)
# Verb-object + quantity: "推荐 50 家" ("recommend 50"), "给我推荐一批客户" ("recommend a batch of customers"), "再来一批" ("another batch"), "top 20".
# The bare "top" in the old word list was a substring match — stop / topic / desktop / laptop all hit,
# so any sentence containing them was judged as finding customers. Here (?<![a-z]) is used instead of \b:
# \b fails in "推荐top20" because the preceding character is CJK (also \w).
_LEAD_PATTERNS = (
    re.compile(r"(找|推荐|获取|来|搞)\s*(一批|\d+\s*[家个])"),
    re.compile(r"^(下一批|换一批|还有吗|还有么)$"),
    re.compile(r"^(还有|再来|再给|再找).{0,8}(一批|一些|一点|类似|客户|企业|公司|线索)"),
    re.compile(r"(?<![a-z])top\s*\d+"),
    # 「有哪些企业需要 GEO 服务?」 ("which companies need GEO services?") — asks for "a batch of target customers", not what we sell.
    # It used to hit geo in _SERVICE_SCOPE_KW first and be judged profile, so the deterministic lead flow never started,
    # and every later reply fell to the model (production, 2026-08-26, ending in a 60s timeout).
    # Kept tight: requires a question word + 企业/客户 ("company/customer") + 需要/适合 ("need/suit") together; history lookups like 「推荐过哪些公司」 ("which companies were recommended")
    # are picked up first by _LOOKUP_KW, which comes earlier.
    re.compile(r"(哪些|哪几家|什么样的)[^。?？]{0,12}(企业|公司|客户|品牌)[^。?？]{0,12}(需要|想要|适合|正在找|要做)"),
)
_DRAFT_KW = ("邮件", "短信", "电话", "话术", "草稿", "触达", "外呼", "私信")
# History lookups: questions like "发过哪些" ("what was sent") / "推荐过哪些" ("what was recommended"). Still ordered before _LEAD_KW — _LEAD_KW no longer contains
# generic nouns, but phrases like "推荐客户/推荐企业" ("recommend customers/companies") in "推荐过哪些公司" may still hit, so the order stays.
_LOOKUP_KW = (
    "发过", "发出去", "发了吗", "发了没", "已发送", "发送情况", "发送记录", "发送状态",
    "打开率", "有没有人看", "有人看吗", "阅读情况", "退信", "失败的",
    "推荐过", "之前推荐", "上次推荐", "上次找", "找过哪些", "历史推荐", "跟进到哪",
    "搜索出来过", "搜出来过", "找出来过", "一共有多少", "多少家企业", "几封邮件", "几家企业",
    "刚才发", "今天发", "今日发", "都发了", "发了多少", "发送了多少", "给多少企业发送", "多少企业发送",
)
# 「批量发」 ("bulk send") was added on 2026-09-13: design 4.7's boundary table lists it alongside 「群发」 ("mass send"), but the word list only had
# 「群发」, so 「批量发一下」 ("bulk send it") actually fell to chat — saying the same thing differently bypassed the compliance branch.
_COMPLIANCE_KW = ("伪造", "改分", "绕过", "爬手机号", "私人微信", "群发", "批量发", "退订", "黑名单")
# FAQ: questions about pricing / capabilities / how to cooperate. These all used to fall to chat with the model as fallback, while the prompt
# required steering to "audit / find customers", so even pricing questions got brushed off with "run an audit first". Now a separate class with direct answers.
_FAQ_KW = (
    "多少钱", "怎么收费", "收费", "价格", "报价", "费用", "套餐", "订阅", "试用", "免费吗",
    "怎么合作", "合作方式", "你们是做什么", "有什么功能", "支持哪些", "能做什么",
    "和竞品", "对比", "区别是什么", "优势是什么", "案例",
)
_SHORT_GREETINGS = {"你好", "您好", "你好啊", "您好啊", "hello", "hi", "hey", "在吗", "在不在"}
_IDENTITY_QUESTIONS = {
    "你是谁",
    "你是谁啊",
    "你能做什么",
    "你是干嘛的",
    "你是什么工具",
    "你是什么工具呢",
    "你是什么助手",
    "你是哪个助手",
    "你是什么agent",
    "你是哪个agent",
    "你有什么用",
    "你可以做什么",
    "介绍一下你自己",
}
_IDENTITY_PATTERNS = (
    re.compile(r"^你(这边|这里)?(有|具备|支持|会|能|可以).{0,8}(能力|功能|工具|事情|什么|干嘛|做啥|做什么)"),
    re.compile(r"^你(这边|这里)?(是|属于).{0,8}(什么|啥|哪个).{0,4}(工具|助手|agent)"),
    re.compile(r"^你(能|可以).{0,6}帮我.{0,6}(什么|做什么|做啥|干嘛)"),
)
_LOW_VALUE_CHATS = {
    "讲个笑话",
    "写首诗",
    "今天天气怎么样",
    "天气怎么样",
    "随便聊聊",
    "没事",
    "测试",
    "test",
}
# Leading/trailing punctuation/symbols are all stripped before comparison. Only `，,。.!！?？~～` used to be stripped —
# 「hello/」 missed the greeting list because of one extra slash and fell to the model for a full reply
# (production, 2026-08-23: in the same session hello/ went to the model and hello? to the guide card, two personas).
_EDGE_PUNCT = re.compile(r"^[^\w\u4e00-\u9fff]+|[^\w\u4e00-\u9fff]+$")


def _short_text(text: str) -> str:
    return _EDGE_PUNCT.sub("", (text or "").strip().lower())


def is_short_greeting(text: str) -> bool:
    return _short_text(text) in _SHORT_GREETINGS


def is_identity_question(text: str) -> bool:
    short = _short_text(text)
    return short in _IDENTITY_QUESTIONS or any(p.search(short) for p in _IDENTITY_PATTERNS)


def is_low_value_chat(text: str) -> bool:
    return _short_text(text) in _LOW_VALUE_CHATS


def should_use_guide_reply(text: str) -> bool:
    return is_short_greeting(text)


def route_intent(text: str) -> str:
    t = (text or "").lower()
    if any(k in t for k in _COMPLIANCE_KW):
        return "compliance"
    # FAQ comes before outreach/lead: "你们怎么收费" ("how do you charge") contains "费" ("fee") and nothing else, but
    # "邮件功能多少钱" ("how much is the email feature") hits both _DRAFT_KW and _FAQ_KW — that's a pricing question, not a request to write email.
    # History lookups come first: such sentences almost always also hit _LEAD_KW (企业/公司/推荐) and _DRAFT_KW (邮件 "email"),
    # and placed later they'd be grabbed by those.
    if any(k in t for k in _LOOKUP_KW):
        return "lookup"
    if any(k in t for k in _FAQ_KW):
        return "faq"
    if any(k in t for k in _DRAFT_KW):
        return "outreach"
    if any(k in t for k in _LEAD_KW) or any(p.search(t) for p in _LEAD_PATTERNS):
        return "lead"
    if any(k in t for k in _SERVICE_SCOPE_KW) and not any(k in t for k in ("报告", "看报告", "读报告", "生成报告", "诊断报告", "分数", "评分")):
        return "profile"
    if any(k in t for k in _ICP_KW):
        return "profile"
    if any(k in t for k in _REPORT_STRONG_KW):
        return "report"
    if any(k in t for k in _REPORT_URL_KW) and any(k in t for k in ("分析", "检测", "诊断", "报告")):
        return "report"
    return "chat"
