"""Intent routing -- keyword fast path + API Rule Guard.

Keyword routing stays coarse-grained for compatibility; the Rule Guard added at the API entry refines high-confidence business input
into the Router taxonomy, so plain chat doesn't wrongly open cards and history queries don't wrongly run new recommendations.

Run:
    venv/bin/python -m pytest tests/sales_agent/test_intent_routing.py -q
"""
from __future__ import annotations

import pytest

from agents.sales_agent.agent import route_intent, should_use_guide_reply
from agents.sales_agent.api import _rule_guard_intent


@pytest.mark.parametrize("text", [
    "你们怎么收费", "多少钱", "有免费试用吗", "价格是多少", "能做什么",
    "支持哪些平台", "怎么合作", "和竞品的区别是什么", "有案例吗",
])
def test_faq_questions_route_to_faq(text):
    assert route_intent(text) == "faq", text


def test_price_question_beats_outreach_keyword():
    """「邮件功能多少钱」 ("how much is the email feature") contains both 「邮件」 ("email") and 「多少钱」 ("how much") -- it's a pricing question, not a request to write email."""
    assert route_intent("邮件功能多少钱") == "faq"


@pytest.mark.parametrize("text,expected", [
    ("帮我写封邮件", "outreach"),
    ("给我推荐一批客户", "lead"),
    ("帮我找客户", "lead"),
    ("检测一下这个官网", "report"),
    ("能不能帮我爬手机号", "compliance"),
])
def test_existing_intents_unchanged(text, expected):
    """Adding faq / Router Guard must not skew the old keyword routing."""
    assert route_intent(text) == expected, text


@pytest.mark.parametrize("text", ["你好", "hello", "hi", "在吗"])
def test_greetings_still_shortcut(text):
    assert should_use_guide_reply(text)


@pytest.mark.parametrize("text", [
    "你是谁", "你能做什么", "你是什么工具呢？", "你有什么能力呢？",
    "你有哪些功能？", "你会什么？", "你能帮我做什么？", "你吃饭了吗？", "你喝水了嘛？",
])
def test_non_business_chat_goes_to_model_before_fallback(text):
    """Capability explanations/personified small talk let the model reply naturally first; on failure the api fallback catches it."""
    assert not should_use_guide_reply(text)


def test_greeting_shortcut_ignores_trailing_punctuation():
    assert should_use_guide_reply("hello?") and should_use_guide_reply("你好!")


# --- Querying history must take priority over running something new ---


@pytest.mark.parametrize("text", [
    "都发过哪些企业?", "某某公司发了吗", "哪些失败的", "有没有人看",
    "之前推荐过哪些公司", "上次找的那批跟进到哪了", "找过哪些企业",
])
def test_lookup_wins_over_lead(text):
    """Such sentences almost always hit both _LEAD_KW (company/firm/recommend) and _DRAFT_KW (email).

    2026-08-23 production: 「都发过哪些企业?」 ("which companies have I sent to?") was classified as lead, the model ran recommend_companies,
    and the user, who asked about history, got a profile card. History queries must rank before every "run".
    """
    assert route_intent(text) == "lookup"


@pytest.mark.parametrize("text", ["帮我找客户", "推荐 50 家工业制造企业", "获取客户名单"])
def test_real_lead_requests_still_route_to_lead(text):
    assert route_intent(text) == "lead"


@pytest.mark.parametrize("text", ["继续推荐", "再推荐一批", "在推荐一批", "继续找客户", "下一批", "换一批", "还有吗", "还有类似的吗"])
def test_continue_recommendation_requests_route_to_lead(text):
    assert route_intent(text) == "lead"
    intent, reason, final = _rule_guard_intent(text)
    assert intent == "lead_recommendation"
    assert reason == "continue_recommendation"
    assert final is True


@pytest.mark.parametrize("text", [
    "现在搜索出来过多少企业？", "刚才发了几封邮件", "今天发了多少封", "一共有多少家企业",
])
def test_counting_questions_are_lookup(text):
    """Asking "how many" is a history query. On 2026-08-23 production 「现在搜索出来过多少企业？」 ("how many companies have been found so far?") was classified as lead,
    the model ran a new recommendation and popped the already-confirmed profile card again."""
    assert route_intent(text) == "lookup"


# --- API Rule Guard: high-confidence input is refined deterministically first ---


@pytest.mark.parametrize("text,expected", [
    ("我的画像是什么", "seller_profile_lookup"),
    ("用户画像是啥", "seller_profile_lookup"),
    ("今天发了几封邮件", "delivery_lookup"),
    ("哪些公司点开了", "delivery_lookup"),
    ("发出的邮箱标题是什么", "delivery_lookup"),
    ("今天最后一封信的内容", "delivery_lookup"),
    ("今天最后三封邮件的标题", "delivery_lookup"),
    ("昨天推荐过哪些企业", "recommendation_lookup"),
    ("上次推荐了多少家公司", "recommendation_lookup"),
    ("推荐 50 家工业制造企业", "lead_recommendation"),
    ("帮我找客户", "lead_recommendation"),
    ("检测一下这个官网", "report_detection"),
    ("生成官网检测报告", "report_detection"),
    ("能不能帮我爬手机号", "compliance"),
])
def test_rule_guard_returns_fine_business_intents(text, expected):
    intent, _reason, final = _rule_guard_intent(text)
    assert intent == expected
    assert final is True


def test_outreach_keyword_is_no_longer_final():
    """「帮我写封邮件」 ("write me an email") is still outreach_draft, but **no longer final** (scheduled send design (2)).

    Keywords can't tell "write one" from "send this batch out", and the two differ in cost by an order of magnitude --
    the latter really sends mail. So Rule Guard (1) only takes high-confidence phrases (send verb AND time/scheduling word),
    the rest goes to the Router Agent, and (2b) forces a downgrade for "model says send, but the sentence has no time".

    If this is changed back to `final is True`, (2) is effectively undone: every send request would be treated as writing a draft.
    """
    intent, reason, final = _rule_guard_intent("帮我写封邮件")
    assert (intent, reason) == ("outreach_draft", "outreach_keyword")
    assert final is False


@pytest.mark.parametrize("text", ["你是谁", "你吃饭了吗？", "你喝水了嘛？", "随便聊聊"])
def test_rule_guard_leaves_non_business_chat_to_router_or_chat_agent(text):
    intent, _reason, final = _rule_guard_intent(text)
    assert intent == "chat"
    assert final is False


# --- Scheduled send (1): acceptance 15 (design 4.7) ---
#
# This section has a single bottom line: **what shouldn't be sent must never become a send**.
# Misjudged as a draft, the user just says one more sentence; misjudged as a send, the mail is out and can't be recalled.


@pytest.mark.parametrize("text", [
    "下午三点把这批发一下",
    "明早九点发",
    "明天上午十点发送这批邮件",
    "下班前把这批邮件发出去",
    "一小时后发给他们",
    "周五上午十点发送",
    "现在就发",
    # Only a scheduling word, no time of day -- still accepted, then the tool asks for the exact time.
    # Dropping it here falls back to writing a draft, and the user's "send on a schedule" is effectively ignored.
    "定时把这批发了",
    # A vague time still counts as "has time": the correct handling is to ask what time, not downgrade to editing copy.
    "晚点发吧",
])
def test_send_requests_hit_the_rule_guard(text):
    intent, reason, final = _rule_guard_intent(text)
    assert (intent, reason, final) == ("send_schedule_create", "send_schedule_request", True), text


@pytest.mark.parametrize("text,reason", [
    ("三点那批发了吗", "send_schedule_query"),
    ("我有哪些定时任务", "send_schedule_query"),
    ("发送计划什么时候执行", "send_schedule_query"),
    ("定时任务的状态", "send_schedule_query"),
    ("别发了", "send_schedule_cancel"),
    ("先别发", "send_schedule_cancel"),
    ("取消今天的发送计划", "send_schedule_cancel"),
])
def test_schedule_lookup_and_cancel_are_read_only(text, reason):
    """Both status lookup and cancel go to `send_schedule_lookup`.

    Cancel especially must not be judged a write intent: the user said "don't send", and upgrading the route to the
    create branch that "really schedules a batch" is exactly the opposite direction.
    """
    from agents.sales_agent.flow.intent import intent_is_read_only

    intent, got_reason, final = _rule_guard_intent(text)
    assert (intent, got_reason, final) == ("send_schedule_lookup", reason, True), text
    assert intent_is_read_only(intent)


@pytest.mark.parametrize("text", [
    # -- the three sentences named in acceptance 15 --
    "给这家写封邮件",
    "改一下刚才那封",
    "邮件功能多少钱",
    # -- same kind --
    "帮我把邮件写得正式一点",
    "帮我写封邮件给这家公司",
    "能不能定时发邮件",      # asking about capability, not asking to send
    "定时发送怎么用",        # same as above
    "查一下今天的发送",      # a question, not an instruction
    "今天发了几封邮件",      # sent stats, belongs to delivery_lookup
    "这封邮件的标题是什么",
    "帮我群发一批邮件",      # compliance red line, left to compliance to refuse
    "推荐 50 家工业制造企业",
    "你是谁",
])
def test_these_must_never_become_a_send(text):
    """Acceptance 15. **Do not merge this PR if any of these fails** -- a failure means the system sent mail the user never asked to send."""
    intent, _reason, _final = _rule_guard_intent(text)
    assert not intent.startswith("send_schedule"), f"{text!r} 被判成了 {intent}"


# --- The sentence we teach users must work when they say it ---
#
# `_delivery_target_next_step` replies 「要发的话直接说『给 X 生成一封跟进邮件』」 ("to send, just say 'generate a follow-up email for X'").
# It used to say 「开发信」 ("outreach letter"), and 「开.{0,8}信」 is exactly the open-rate query check -- saying it verbatim routed to
# stats lookup, and with `final=True` in the Guard, the Router never got a chance to be asked.
# The whole system now uniformly says 「跟进邮件」 ("follow-up email") (the Router prompt, compliance fallback and identity reply already did).
# Both groups below guard the same thing: **any phrasing that appears in a hint must reach the flow it promises.**


@pytest.mark.parametrize("text", [
    "给某某公司生成一封跟进邮件",   # exactly the sentence from the hint
    "帮我写个跟进邮件",
    "跟进邮件写得正式一点",
])
def test_the_phrase_we_teach_users_routes_to_drafting(text):
    intent, _reason, _final = _rule_guard_intent(text)
    assert intent == "outreach_draft", f"{text!r} 被判成了 {intent}"


@pytest.mark.parametrize("text", [
    "明早九点发跟进邮件",
    "下午三点把跟进邮件发出去",
    "明天上午十点发触达邮件",
])
def test_a_modifier_before_the_object_does_not_block_scheduling(text):
    """In 「发跟进邮件」 ("send a follow-up email") two characters sit between verb and object, which the old `_SEND_OBJECT_RE` didn't catch."""
    intent, reason, final = _rule_guard_intent(text)
    assert (intent, reason, final) == ("send_schedule_create", "send_schedule_request", True), text


def test_bare_open_verb_no_longer_swallows_the_detection_entry():
    """「打开」 ("open") used to be a bare email-domain keyword, so 「打开这家公司的官网」 ("open this company's website") also counted as delivery.

    The real cost is in `_rule_guard_intent`: the URL-based audit entry is
    `_url_from_text(...) and not _is_email_delivery_text(...)` -- one misjudgment and the website audit can't be entered at all.
    """
    from agents.sales_agent.api import _is_email_delivery_text

    assert not _is_email_delivery_text("打开这家公司的官网")
    assert _rule_guard_intent("打开 https://example.com 检测一下")[0] == "report_detection"


@pytest.mark.parametrize("text", ["哪些公司点开了", "有几家阅读过", "今天开了几封信", "哪些公司开信了"])
def test_real_open_queries_still_route_to_delivery(text):
    """Tightening bare verbs must not also drop real open-rate queries -- these sentences don't contain the word 「邮件」 ("email") at all."""
    intent, _reason, _final = _rule_guard_intent(text)
    assert intent == "delivery_lookup", f"{text!r} 被判成了 {intent}"


# --- (2c) send instructions without a time must not be downgraded to "answer stats" ---
#
# 「发出去」 ("send out") is in both `_LOOKUP_KW` and `_SEND_VERBS`: keyword routing says lookup,
# `_explicit_stats_domain()` says delivery because of 「邮件」 ("email"), and `final=True` settles it.
# The user gave a send instruction and got a send-volume stats card, with the Router locked out and no chance to ask a follow-up.
#
# The gate "no time, no plan" is right and stays; only the downgrade target changes --
# hand back to the Router to decide "ask for time / write draft / schedule"; (2b) still guarantees nothing is scheduled without an extracted time.


@pytest.mark.parametrize("text", ["把这批邮件发出去", "帮我发出去"])
def test_send_command_without_time_goes_back_to_the_router(text):
    intent, reason, final = _rule_guard_intent(text)
    assert (intent, reason, final) == ("outreach_draft", "send_command_without_time", False), text


@pytest.mark.parametrize("text", ["今天发了几封邮件", "都发过哪些企业", "哪些失败的", "发送记录看一下"])
def test_delivery_questions_are_still_final_lookups(text):
    """(2c) only lets "instructions" through; not a single "asking about results" sentence may loosen -- otherwise every stats query goes through the model."""
    intent, _reason, final = _rule_guard_intent(text)
    assert intent in {"delivery_lookup", "lookup"}, f"{text!r} 被判成了 {intent}"
    assert final is True, text


# --- Greeting detection must be robust to punctuation ---


@pytest.mark.parametrize("text", [
    "hello/", "hello?", "Hello!", "hello。", "hello～", "  hello  ",
    "你好~", "你好.", "在吗。", "hi!", "hi/",
])
def test_greetings_survive_punctuation(text):
    """It used to strip only `，,。.!！?？~～`; 「hello/」 with one extra slash didn't match and went through a full model reply --
    in the same session hello/ went to the model and hello? to the guide card: two personas (2026-08-23 production)."""
    from agents.sales_agent.agent import should_use_guide_reply
    assert should_use_guide_reply(text), f"{text!r} 应该走确定性引导"


@pytest.mark.parametrize("text", ["帮我找客户", "都发过哪些企业?", "推荐 50 家企业", "怎么收费"])
def test_real_requests_are_not_greetings(text):
    """Don't block messages with real content as greetings."""
    from agents.sales_agent.agent import should_use_guide_reply
    assert not should_use_guide_reply(text)


# --- 2026-08-25 production: 「有哪些开信的公司?」 ("which companies opened the email?") was classified as lead and returned our profile card ---


@pytest.mark.parametrize("text", [
    "有哪些开信的公司？", "哪些公司点开了", "看过的企业有哪些", "有反应的公司",
    "我们公司是做什么的", "这几家企业怎么样",
])
def test_generic_nouns_no_longer_force_lead(text):
    """Generic nouns (company / firm / customer / recommend) no longer classify a sentence as lead on their own.

    Classifying as lead costs more than "inaccurate classification": `build_sales_agent("lead")` attaches
    `RequireFirstToolCall("recommend_companies")`, forcing the recommendation tool on the first step,
    so a need_more_info our-profile card inevitably pops up -- the user asked about email opens
    and got "please confirm our profile first" (2026-08-25 production).

    Adding 「开信」 ("opened") to _LOOKUP_KW doesn't fix it; the next user will say "clicked", "viewed", "responded".
    If unrecognized it should land in chat and go to the model classifier in flow/intent.py.
    """
    assert route_intent(text) != "lead", text


@pytest.mark.parametrize("text", ["stop 20", "topic 3", "desktop 4", "laptop 15"])
def test_bare_top_substring_no_longer_matches(text):
    """The word list used to contain a bare "top", matched as a substring -- stop / topic / desktop / laptop were all classified as lead."""
    assert route_intent(text) != "lead", text


@pytest.mark.parametrize("text", [
    "帮我找客户", "推荐 50 家工业制造企业", "推荐50家企业", "给我推荐一批客户",
    "再来一批", "获取客户名单", "要 top 20 的企业", "找一批做外贸的", "找企业",
])
def test_high_confidence_lead_phrases_still_shortcut(text):
    """Tightening the word list must not let real find-customers requests slip through -- that wastes a model classification call."""
    assert route_intent(text) == "lead", text
