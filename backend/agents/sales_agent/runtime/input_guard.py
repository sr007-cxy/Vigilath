"""Sales Agent input understanding gate layer.

Only makes high-certainty judgments on input signals; no model calls, no DB queries. The goal is to first block several kinds of production incidents:

- card button text being written into profiles/slots;
- user questions being taken as answers to the current step;
- short follow-ups like 「哪几家」 ("which ones") losing the previous turn's stats metric definition;
- metric-definition corrections like 「推荐企业不是发送企业」 ("recommended companies aren't sent companies") falling into the model fallback and failing.
"""
from __future__ import annotations

import re
from typing import Any


# Action button text on cards. They mean "what to do", not answers to the current question —
# landing in a profile they'd become 「更适合优先服务找客户」 (nonsense like "better suited to prioritize serving find customers").
FLOW_ACTION_TOKENS = frozenset({
    "继续", "确认", "确认选择", "下一步", "继续找企业", "继续找客户",
    "返回上一步", "更新画像", "更新资料", "更新企业信息", "修改画像", "修改资料", "重新填写",
    "找客户", "找企业", "推荐企业", "推荐客户", "获取客户", "获取顾客",
    "保存为本次推荐画像", "保存本次推荐画像", "保存推荐画像", "保存画像", "本次不保存", "不保存", "不用保存", "直接推荐", "跳过保存",
    "先检测官网", "检测官网", "官网检测", "诊断官网", "官网诊断", "生成诊断报告", "生成报告", "跳过",
})

_ORPHAN_FLOW_ACTION_TOKENS = frozenset({"继续", "确认", "确认选择", "下一步", "跳过"})
_DETECTION_ACTION_TOKENS = frozenset({"先检测官网", "检测官网", "官网检测", "诊断官网", "官网诊断", "生成诊断报告", "生成报告"})
_LEAD_ACTION_TOKENS = frozenset({"找客户", "找企业", "推荐企业", "推荐客户", "获取客户", "获取顾客", "继续找企业", "继续找客户"})
_ACTION_EDGE_CHARS = " ，,。.!！?？~～"

_QUESTION_MARKS = ("?", "？")
_QUESTION_WORDS = ("哪些", "哪个", "什么", "怎么", "如何", "多少", "是不是", "有没有", "能不能", "吗")

_DETAIL_FOLLOWUP_WORDS = (
    "具体", "具体哪些", "哪一家", "哪家公司", "哪家", "哪个企业", "哪个公司", "哪些", "哪几",
    "名单", "明细", "列表", "列举", "列出来", "展示", "展开", "详情", "记录", "看一下", "给我看", "是谁", "是哪个", "是哪",
)
_DELIVERY_FOLLOWUP_DETAIL_WORDS = _DETAIL_FOLLOWUP_WORDS
_DELIVERY_FOLLOWUP_SCOPE_ALL_WORDS = ("不是今天", "不看今天", "不是当天", "全部", "所有", "累计", "历史")
_DELIVERY_FOLLOWUP_SCOPE_TODAY_WORDS = ("今天", "今日", "当天")
_DELIVERY_FOLLOWUP_SCOPE_YESTERDAY_WORDS = ("昨天", "昨日")
_DELIVERY_FOLLOWUP_SCOPE_DAY_BEFORE_YESTERDAY_WORDS = ("前天", "前日")
_DELIVERY_KINDS = {"report_visit", "open", "failed", "sent"}

_RECOMMENDATION_TERMS = ("推荐", "推荐的", "推荐企业", "推荐名单", "推荐记录", "推荐过")
_DELIVERY_TERMS = ("发送", "发了", "发过", "发信", "邮件", "触达", "投递", "寄出")
_SEPARATION_TERMS = ("不是", "不等于", "不一样", "不是一个", "搞混", "混了", "混在一起")
_ASSISTANT_IDENTITY_TERMS = (
    "你是谁", "你是谁啊", "你是什么工具", "你是啥工具", "你是什么助手", "你是哪个助手",
    "你是什么agent", "你是哪个agent", "你能做什么", "你可以做什么", "你有什么用", "介绍一下你自己",
)
_ASSISTANT_CAPABILITY_PATTERNS = (
    re.compile(r"^你(这边|这里)?(有|具备|支持|会|能|可以).{0,8}(能力|功能|工具|事情|什么|干嘛|做啥|做什么)"),
    re.compile(r"^你(这边|这里)?(是|属于).{0,8}(什么|啥|哪个).{0,4}(工具|助手|agent)"),
    re.compile(r"^你(能|可以).{0,6}帮我.{0,6}(什么|做什么|做啥|干嘛)"),
)
_SELLER_PROFILE_IDENTITY_TERMS = ("我是谁", "我是誰", "你知道我是谁", "你知道我是誰")
_REFERENCE_QUESTION_TERMS = ("我说谁", "我说的是谁", "这个是谁", "这是哪个", "这是哪家", "他是谁", "她是谁", "它是谁", "指的是谁")
_IDENTITY_OR_REFERENCE_TERMS = _ASSISTANT_IDENTITY_TERMS + _SELLER_PROFILE_IDENTITY_TERMS + _REFERENCE_QUESTION_TERMS
_CASUAL_CHAT_PATTERNS = (
    re.compile(r"^你.{0,10}(吃饭|吃过饭|喝水|休息|睡觉|困不困|累不累|累吗|开心|快乐|忙不忙|辛苦|上班|下班)"),
    re.compile(r"^(吃饭|喝水|休息|睡觉|累不累|开心|快乐|忙不忙|辛苦).{0,8}(吗|嘛|么)?$"),
)


def normalize_flow_action_text(message: str) -> str:
    """Normalize leading/trailing punctuation of card action text, so the same button word doesn't go to the model because of punctuation."""
    return str(message or "").strip().lower().strip(_ACTION_EDGE_CHARS)


def resolve_orphan_flow_action(message: str, *, step: str = "", detection_step: str = "") -> str:
    """Recognize card actions with no pending context. Returns the action name; an empty string means it's not an orphan action."""
    if str(step or "").strip() or str(detection_step or "").strip():
        return ""
    action = normalize_flow_action_text(message)
    return action if action in _ORPHAN_FLOW_ACTION_TOKENS else ""


def resolve_detection_action(message: str) -> str:
    """Recognize report audit entry actions. Returns the action name; an empty string means it's not an audit action."""
    action = normalize_flow_action_text(message)
    return action if action in _DETECTION_ACTION_TOKENS else ""


def resolve_lead_action(message: str) -> str:
    """Recognize find-customers entry actions. Returns the action name; an empty string means it's not a find-customers action."""
    action = normalize_flow_action_text(message)
    return action if action in _LEAD_ACTION_TOKENS else ""


def verify_detection_keywords_answer(message: str) -> list[str]:
    """Whether this sentence can be used as report audit keywords. Returns reasons for rejection; an empty list means it can."""
    text = normalize_flow_action_text(message)
    if not text:
        return ["空消息"]
    if text in FLOW_ACTION_TOKENS or text in _ORPHAN_FLOW_ACTION_TOKENS:
        return ["这是卡片动作文字,不是品牌词或行业关键词"]
    return []


def verify_slot_answer(step: str, message: str) -> list[str]:
    """Whether this sentence can be treated as "an answer to the step's card". Returns reasons for rejection; an empty list means it can."""
    if not str(step or "").strip():
        return []
    text = str(message or "").strip()
    if not text:
        return ["空消息"]
    if text.endswith(_QUESTION_MARKS):
        return ["这是一句提问,不是对当前问题的回答"]
    if any(w in text for w in _QUESTION_WORDS):
        return ["含疑问词,更像是在问问题而不是回答"]
    if normalize_flow_action_text(text) in FLOW_ACTION_TOKENS:
        return ["这是卡片上的动作按钮文字,不是答案"]
    return []


def resolve_delivery_status_followup(message: str, state: dict[str, Any] | None) -> dict[str, str] | None:
    """Resolve short follow-ups like “具体是哪一家” ("which one exactly") into a detail request for the previous turn's outreach status query."""
    if not isinstance(state, dict):
        return None
    kind = str(state.get("kind") or "").strip()
    if kind not in _DELIVERY_KINDS:
        return None
    text = str(message or "").strip().lower()
    if not text or identity_or_reference_kind(text):
        return None
    has_detail_signal = any(k in text for k in _DELIVERY_FOLLOWUP_DETAIL_WORDS) or bool(
        re.search(r"哪\s*[0-9一二两三四五六七八九十百千]+\s*家", text)
    )
    has_scope_signal = any(k in text for k in _DELIVERY_FOLLOWUP_SCOPE_ALL_WORDS + _DELIVERY_FOLLOWUP_SCOPE_TODAY_WORDS + _DELIVERY_FOLLOWUP_SCOPE_YESTERDAY_WORDS + _DELIVERY_FOLLOWUP_SCOPE_DAY_BEFORE_YESTERDAY_WORDS)
    # Only catch short follow-ups when the previous turn was confirmed to be a delivery_query; avoid forcing DB queries on ordinary chit-chat.
    if not has_detail_signal and not has_scope_signal:
        return None
    has_today = any(k in text for k in _DELIVERY_FOLLOWUP_SCOPE_TODAY_WORDS)
    has_yesterday = any(k in text for k in _DELIVERY_FOLLOWUP_SCOPE_YESTERDAY_WORDS)
    has_day_before = any(k in text for k in _DELIVERY_FOLLOWUP_SCOPE_DAY_BEFORE_YESTERDAY_WORDS)
    if any(k in text for k in _DELIVERY_FOLLOWUP_SCOPE_ALL_WORDS):
        scope = "all"
    elif has_today and has_yesterday and has_day_before:
        scope = "day_before_yesterday_today"
    elif has_today and has_yesterday:
        scope = "yesterday_today"
    elif has_yesterday and has_day_before:
        scope = "day_before_yesterday_yesterday"
    elif has_today and has_day_before:
        scope = "day_before_yesterday_today"
    elif has_day_before:
        scope = "day_before_yesterday"
    elif has_yesterday:
        scope = "yesterday"
    elif has_today:
        scope = "today"
    else:
        scope = str(state.get("scope") or "all")
    result = {"kind": kind, "scope": scope}
    if has_detail_signal and str(state.get("mode") or "") == "count":
        result["mode"] = "detail"
    return result


def is_detail_followup(message: str) -> bool:
    """Recognize detail follow-ups like “能具体列出来吗” ("can you list them specifically") that depend on the previous turn's metric definition."""
    text = str(message or "").strip().lower()
    if not text:
        return False
    return any(k in text for k in _DETAIL_FOLLOWUP_WORDS) or bool(
        re.search(r"哪\s*[0-9一二两三四五六七八九十百千]+\s*家", text)
    )


def is_identity_or_reference_question(message: str) -> bool:
    """Recognize identity/reference/anthropomorphic chit-chat questions that need no tools."""
    return bool(identity_or_reference_kind(message))


def identity_or_reference_kind(message: str) -> str:
    """Return the type of an ordinary identity/reference question; an empty string means it's not that kind of fallback question."""
    text = str(message or "").strip().lower()
    if not text:
        return ""
    if any(k in text for k in _ASSISTANT_IDENTITY_TERMS) or any(p.search(text) for p in _ASSISTANT_CAPABILITY_PATTERNS):
        return "assistant"
    if any(k in text for k in _SELLER_PROFILE_IDENTITY_TERMS):
        return "seller"
    if any(k in text for k in _REFERENCE_QUESTION_TERMS):
        return "reference"
    if any(p.search(text) for p in _CASUAL_CHAT_PATTERNS):
        return "casual"
    return ""


def is_casual_chat(message: str) -> bool:
    """Recognize anthropomorphic chit-chat needing no tools, to avoid surfacing a generic error when the model path fails."""
    text = str(message or "").strip().lower()
    if not text:
        return False
    return any(p.search(text) for p in _CASUAL_CHAT_PATTERNS)


def resolve_stats_domain_correction(message: str, last_stats: dict[str, Any] | None = None) -> dict[str, str] | None:
    """Recognize metric-definition corrections between recommendation stats and email stats.

    Such sentences are neither a new recommendation nor a profile write, for example:
    - 推荐的企业不是我发送的 ("the recommended companies aren't the ones I sent to")
    - 不是发送,我是问推荐的企业 ("not sending, I'm asking about recommended companies")
    - 不是推荐,我是问发过邮件的企业 ("not recommending, I'm asking about companies that were emailed")
    """
    text = str(message or "").strip().lower()
    if not text:
        return None
    has_recommendation = any(k in text for k in _RECOMMENDATION_TERMS)
    has_delivery = any(k in text for k in _DELIVERY_TERMS)
    has_separation = any(k in text for k in _SEPARATION_TERMS)
    if not has_separation:
        return None

    compact = text.replace(" ", "")
    if any(k in compact for k in ("不是发送", "不是发过", "不是邮件", "不是触达")) and has_recommendation:
        return {"type": "stats_domain_correction", "preferred_domain": "recommendation"}
    if any(k in compact for k in ("不是推荐", "不是推荐企业", "不是推荐名单")) and has_delivery:
        return {"type": "stats_domain_correction", "preferred_domain": "delivery"}
    if has_recommendation and has_delivery:
        previous = ""
        if isinstance(last_stats, dict):
            previous = str(last_stats.get("domain") or "")
        return {"type": "stats_domain_separation", "preferred_domain": previous}
    return None
