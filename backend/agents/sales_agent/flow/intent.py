"""Intent classification: the rule Guard takes the fast path; uncertain cases go to the Router Agent.

Originally there were only keywords, and `_LEAD_KW` contained generic nouns like "企业" "公司" "推荐" "客户" ("enterprise", "company", "recommend", "customer") —
any sales sentence hit them, so 「现在搜索出来过多少企业?」 ("how many companies have been found so far?") was judged as "find customers",
and the model ran a new recommendation (production, 2026-08-23); 「有哪些开信的公司?」 ("which companies opened the email?") likewise got
our own profile card (production, 2026-08-25). Adding words can't fix it: after adding "搜索出来过" ("found so far"), next time "统计一下我找了
多少家" ("count how many I found") still slips through; after adding "开信" ("opened"), next time "点开的" / "有反应的" ("clicked" / "responded") still slip through.

The entry now has four layers:
- the rule Guard hits high-confidence business/chat boundaries -> used directly, no model call
- the Router Agent only outputs a structured intent -> doesn't answer the user, query the DB or call business tools
- the Deterministic Business Flow queries the DB / issues cards / writes state based on the intent
- the Chat Agent only handles ordinary chat, with tools=[]

Model unavailable, timeout, invalid return -> fall back to the keyword result; intent classification must not block the whole turn.
"""
from __future__ import annotations

import asyncio
import os

# Used by ②b. `time_parse` depends only on the standard library, so placing it at the module top doesn't cause circular imports
# (`agents/sales_agent/__init__.py` is empty).
from agents.sales_agent.time_parse import has_time_expression

# Compatible with the old coarse intents, while giving the Router Agent a sufficiently fine business taxonomy.
LEGACY_INTENTS = ("compliance", "lookup", "faq", "outreach", "lead", "chat", "profile", "report")
ROUTER_INTENTS = (
    "chat",
    "faq",
    "seller_profile_lookup",
    "seller_profile_update",
    "delivery_lookup",
    "recommendation_lookup",
    "lead_recommendation",
    "report_detection",
    "outreach_draft",
    "send_schedule_create",
    "send_schedule_lookup",
    "compliance",
    "unclear",
)
INTENTS = tuple(dict.fromkeys(LEGACY_INTENTS + ROUTER_INTENTS))

# Read-only intents: query, ask, chit-chat. Write intents really run filters and really send emails, costing hundreds of times more —
# this attribute is part of the classification result and shouldn't depend on word-list luck.
READ_ONLY_INTENTS = frozenset({
    "lookup",
    "faq",
    "chat",
    "seller_profile_lookup",
    "delivery_lookup",
    "recommendation_lookup",
    # Checking plan status is read-only. **Plan creation (send_schedule_create) isn't here** — it really schedules a batch of emails.
    "send_schedule_lookup",
    "unclear",
})

# Fine-grained intent -> old flow/agent scene. This lets the Router be finer without overturning the existing deterministic flow at once.
_COARSE_INTENT_MAP = {
    "lead_recommendation": "lead",
    "delivery_lookup": "lookup",
    "recommendation_lookup": "lookup",
    "seller_profile_lookup": "lookup",
    "seller_profile_update": "profile",
    "report_detection": "report",
    "outreach_draft": "outreach",
    "send_schedule_create": "outreach",
    "send_schedule_lookup": "lookup",
    "unclear": "chat",
}

# Keyword judgments of these classes don't count: chat is the fallback bucket, and even with lead tightened to high-confidence phrases,
# something like 「推荐企业」 ("recommend companies") may be a history question ("推荐过哪些" "which were recommended") — the Router's rule is to pick read-only when unsure.
#
# `outreach_draft` was added on 2026-09-13 (② of design 4.7). **Without it ② is an empty promise**:
# ② changed the `keyword == "outreach"` branch from final=True to False, meaning to hand this branch back to the
# Router Agent; but `_resolve_message_intent` only builds the classifier when `needs_model_classification()` is true,
# otherwise `resolve_intent` returns the keyword result as-is — changing final without adding a line here
# shows up as "the Router is still never asked", while all regression cases stay green.
#
# Both the coarse `outreach` and the fine `outreach_draft` must be present: the Guard hands back the fine one,
# but both `lead`/`lead_recommendation` names are accepted, so this follows the same style, lest some day a
# call site passes the coarse name and the gate silently fails.
_AMBIGUOUS = frozenset({"lead", "chat", "lead_recommendation", "outreach", "outreach_draft"})

_CLASSIFY_PROMPT = """你是 Sales Agent 的 Router Agent。你只做一件事:判断用户这句话属于哪个意图。

硬性边界:
- 你只输出结构化 JSON;不要回答用户。
- 你没有业务工具,不能查库,不能生成推荐卡片,不能写邮件。
- 句子里出现“企业/公司/推荐/邮件”不代表要执行动作。
- 问已经发生过什么是 lookup;要“再做一次/新找一批”才是 lead_recommendation。
- 拿不准时优先选择 chat 或只读 lookup,不要升级成会产生副作用的业务 flow。

可选 intent:
- chat:问候、闲聊、非销售增长问题、无明确诉求。
- faq:问 Sales Agent 能力、价格、怎么合作、产品说明。
- seller_profile_lookup:问“我的画像/用户画像/我方画像/品牌画像是什么”。
- seller_profile_update:要求更新/修改画像或品牌资料。
- delivery_lookup:查邮件发送、开信/阅读、失败、退信、报告访问等已发生记录。
- recommendation_lookup:查历史推荐、推荐过哪些企业、推荐数量。
- lead_recommendation:要新找客户/新推荐企业/再来一批。
- report_detection:要检测官网、生成/查看诊断报告、分析 URL。
- outreach_draft:要写/改跟进邮件、触达话术、私信草稿。**写内容,不涉及什么时候发。**
- send_schedule_create:要把已经有的这批邮件**发出去**,并且说了(或需要说)什么时候发。
- send_schedule_lookup:问定时发送计划排了没有、几点、发了几封,或者要取消/停掉某个计划。
- compliance:要求伪造、绕过限制、群发、抓私人联系方式、退订黑名单违规操作。
- unclear:业务诉求存在,但缺关键对象或口径,需要追问。

典型区分:
- “今天发了几封邮件”“哪些公司开信了” → delivery_lookup。
- “昨天推荐过哪些企业”“上次找了多少家” → recommendation_lookup。
- “推荐 50 家工业制造企业”“帮我找客户” → lead_recommendation。
- “我的画像是什么”“用户画像是啥” → seller_profile_lookup。
- “你吃饭了吗”“你是谁”“随便聊聊” → chat。
- **写内容 vs 发出去,这是两件事**:“给这家写封邮件”“改一下刚才那封”“邮件写得正式点”
  → outreach_draft;“下午三点把这批发一下”“明早九点发”“定时发” → send_schedule_create。
- “三点那批发了吗”“我有哪些定时任务” → send_schedule_lookup;
  “取消今天的发送计划”“别发了” → send_schedule_lookup。
- **“邮件功能多少钱”“能不能定时发邮件”是在问能力,→ faq**,不是要发信。

read_only 必须与 intent 一致:
- true: chat / faq / seller_profile_lookup / delivery_lookup / recommendation_lookup / send_schedule_lookup / unclear
- false: lead_recommendation / seller_profile_update / report_detection / outreach_draft / send_schedule_create / compliance"""


def normalize_intent(intent: str) -> str:
    """Converge the model's/old router's return value into a supported intent."""
    value = str(intent or "").strip().lower()
    aliases = {
        "lead": "lead",
        "recommend": "lead_recommendation",
        "recommend_companies": "lead_recommendation",
        "lookup_delivery": "delivery_lookup",
        "email_lookup": "delivery_lookup",
        "profile_lookup": "seller_profile_lookup",
        "profile_update": "seller_profile_update",
        "report": "report",
        "detect_report": "report_detection",
        "outreach": "outreach",
        "draft": "outreach_draft",
        # Scheduled sending. The model easily emits shorthands like `schedule_send` / `send_schedule`;
        # if not caught they fall into the `picked not in INTENTS` keyword_fallback, showing up as "the model judged right but it didn't take effect".
        "schedule": "send_schedule_create",
        "schedule_send": "send_schedule_create",
        "send_schedule": "send_schedule_create",
        "scheduled_send": "send_schedule_create",
        "send_email": "send_schedule_create",
        "schedule_lookup": "send_schedule_lookup",
        "send_schedule_status": "send_schedule_lookup",
        "send_schedule_cancel": "send_schedule_lookup",
    }
    return aliases.get(value, value)


def coarse_intent_for(intent: str) -> str:
    """Return a coarse intent that the old flow/agent scene understands."""
    value = normalize_intent(intent)
    return _COARSE_INTENT_MAP.get(value, value if value in LEGACY_INTENTS else "chat")


def intent_is_read_only(intent: str) -> bool:
    return normalize_intent(intent) in READ_ONLY_INTENTS


def intent_needs_business_flow(intent: str) -> bool:
    return coarse_intent_for(intent) not in {"chat", "faq"} or normalize_intent(intent) in {
        "seller_profile_lookup",
        "delivery_lookup",
        "recommendation_lookup",
        "unclear",
    }


def needs_model_classification(keyword_intent: str) -> bool:
    return normalize_intent(keyword_intent) in _AMBIGUOUS


def llm_classification_enabled() -> bool:
    return os.environ.get("SALES_AGENT_INTENT_LLM", "").strip() != "0"


def classify_timeout() -> float:
    try:
        return max(1.0, float(os.environ.get("SALES_AGENT_INTENT_TIMEOUT", "3")))
    except ValueError:
        return 3.0


def build_intent_classifier():
    """A small Router Agent that only outputs a structured intent. No tools, no history, no profile."""
    from pydantic import BaseModel, Field
    from pydantic_ai import Agent

    from agents.sales_agent.model import get_sales_agent_model, get_sales_agent_model_settings

    class IntentDecision(BaseModel):
        intent: str
        read_only: bool
        confidence: float = Field(default=0.7, ge=0, le=1)
        needs_business_flow: bool = False
        reason: str = ""

    return Agent(
        get_sales_agent_model("chat"),
        output_type=IntentDecision,
        system_prompt=_CLASSIFY_PROMPT,
        tools=[],
        retries=1,
        model_settings=get_sales_agent_model_settings("chat"),
    )


async def resolve_intent(message: str, keyword_intent: str, classify=None) -> tuple[str, str]:
    """Returns (final intent, decision source). Intent classification must not block the whole turn — any exception falls back to the keyword result."""
    keyword = normalize_intent(keyword_intent)
    if not needs_model_classification(keyword) or not llm_classification_enabled():
        return keyword, "keyword"
    if classify is None:
        return keyword, "keyword"
    try:
        decision = await asyncio.wait_for(classify(message), timeout=classify_timeout())
    except Exception:  # noqa: BLE001 — timeout/model failure/invalid return all fall back to keywords
        return keyword, "keyword_fallback"
    picked = normalize_intent(str(getattr(decision, "intent", "") or ""))
    if picked not in INTENTS:
        return keyword, "keyword_fallback"
    confidence = getattr(decision, "confidence", None)
    try:
        confidence_value = float(confidence) if confidence is not None else 0.7
    except (TypeError, ValueError):
        confidence_value = 0.7
    if confidence_value < 0.35:
        return keyword, "keyword_fallback"
    return _post_check(message, picked)


def _post_check(message: str, picked: str) -> tuple[str, str]:
    """②b post-check: the model says "scheduled sending", but no time can be extracted from this sentence — don't trust it.

    Why not rely on regression cases: accuracy is in the model's hands, which we can't control. Cases only prove "these sentences are right now";
    they can't prove "next time a different phrasing won't judge 'write an email to this company' as sending". And the cost of this misjudgment is
    **a batch of emails really sent**, not a wrong reply.

    Three constraints (design 4.7 ②b):

    1. **Time expressions reuse ①'s copy** — call `time_parse.has_time_expression()` directly,
       the same code as real parsing. Separate word lists would create the blind spot "① doesn't catch, the model catches, ②b downgrades again".
       Vague times (「晚点发」 "send later") count as "present" there, so they never get here — the right handling is to ask for a specific time,
       not downgrade to drafting.
    2. **Only downgrade, never upgrade.** This never changes another intent into `send_schedule_create`;
       a model miss costs the user one more sentence, a misjudgment costs sent emails — the two aren't symmetric.
    3. **Downgrades leave a trace** (`source="post_check_downgrade"`) — without a trace, production only shows
       "the model judges quite accurately", not how much this gate actually blocked, so there's no way to tell whether to loosen it.

    Relation to ①: on a ① hit it's `final=True` and never reaches here, so this only sees sentences where "① didn't catch and
    the model wants to upgrade on its own". ①'s condition is one notch broader than here (it also accepts scheduling words), which looks like two metric definitions, but actually
    covers two disjoint sets of sentences — 「定时把这批发了」 ("send this batch on schedule") is hit directly by ① and never downgraded here.
    """
    if picked != "send_schedule_create":
        return picked, "model"
    if has_time_expression(message):
        return picked, "model"
    return "outreach_draft", "post_check_downgrade"
