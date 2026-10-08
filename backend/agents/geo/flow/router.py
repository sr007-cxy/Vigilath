"""What to do next — pure function, unit-testable; no services, no model calls.

Mirrors agents/sales_agent/flow/router.py.
"""
from __future__ import annotations

from agents.geo.flow.state import SLOT_ORDER, DiagnosisState

BRAND = "brand"
INDUSTRY_TERMS = "industry_terms"
ASKING = "asking"      # Pending card sent, waiting for the user to fill slots (line-lock criterion)
READY = "ready"        # Slots complete, ready to start
RUNNING = "running"    # Already started; this turn should check progress rather than ask
DONE = "done"          # Report produced


def next_step(state: DiagnosisState) -> str:
    """Compute the next step from "what's still missing", not from "what the previous step was".

    The difference matters: driven by "previous step", when the user gives both brand and industry terms in one message,
    the intermediate cards would still pop up one by one; driven by "what's missing", they're skipped automatically.
    """
    if state.stage == DONE:
        return DONE
    # Once started, stop asking for slots — that run is already going with the terms it had,
    # and asking "what are the industry terms" now is a question that no longer means anything.
    if state.report_id and state.stage == RUNNING:
        return RUNNING
    for slot in SLOT_ORDER:
        if not state.filled(slot):
            return slot
    return READY


def blocks_other_lines(state: DiagnosisState) -> bool:
    """Whether this turn must be locked to the GEO line.

    ★ This fixes route_line: the business line is judged **message by message** by keyword, and when the user answers with industry terms
      they may well include sentiment words (「口碑营销」「风险」「差评」 ("reputation marketing", "risk", "bad reviews") are all in _SENTIMENT_KW).
      Once routed to the sentiment line, this turn can't even load run_full_diagnosis' schema,
      and the diagnosis never gets started — users feel "I talked for ages and it never ran".
      While waiting for the user to fill slots, the business line shouldn't be decided by the literal text of this message.

    ★★ The criterion only accepts `stage == "asking"`, **not whether next_step is a slot name**:
       with empty slots next_step is also "brand"; using that would lock **every first message** into the GEO line —
       a user starting fresh with 「今天舆情怎么样」 ("how's sentiment today") wouldn't get sentiment tools.
       Lock only when a pending card was really sent and we're genuinely waiting for an answer.
    """
    return state.stage == ASKING


def preamble(state: DiagnosisState) -> str:
    """The 【当前流程】 ("current flow") prefix block for the model.

    Put in the **user message**, not the system prompt — same reason as api.py's _self_preamble / _admin_preamble:
    the system prompt gets serialized into history, so existing sessions forever use the version at persist time.
    """
    step = next_step(state)
    if step == BRAND:
        return ("【当前流程】用户想跑一键全面诊断,但还没说清是哪个品牌。"
                "先问品牌名,拿到之前不要调 run_full_diagnosis。\n")
    if step == INDUSTRY_TERMS:
        who = f"「{state.brand}」" if state.brand else "这个品牌"
        return (f"【当前流程】正在等用户补{who}的**行业词**,诊断**还没有发起**。"
                "拿到之前不要发起诊断。**不要替他猜是做哪一行的、不要列举各行各业**——"
                "你不知道答案,猜错会让整单八项子检测都拿错问题去问 AI。"
                "调一次 run_full_diagnosis 拿到待填卡,把卡原样交给用户就行,只说一句"
                "「填一下行业词就开跑」。\n")
    if step == RUNNING:
        return (f"【当前流程】品牌「{state.brand}」的诊断单 #{state.report_id} 已经在跑"
                "(整单约 8–20 分钟)。用户问进度就用 get_full_diagnosis 查这一单,"
                "**不要重复发起、不要在同一轮里反复轮询**,也不要把别的品牌的进度说成它的。\n")
    return ""
