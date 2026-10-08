"""The find-customers flow must not be misrouted into the website audit, and if it is, it must be able to get out.

2026-08-27 production: in the find-customers flow the user clicked several card options and the submission was one long paragraph

    「…现有的GEO服务商既做优化、又为自己的优化效果出具**报告**——既当运动员、
     又当裁判员、…、有**官网**但搜索获客一般的企业」 (functional input: card options mentioning "report" and "official website")

The loose tier of `_is_detection_entry` ANDs two word groups, 「检测/诊断/报告」 ("audit/diagnose/report") x 「官网/网站/网址/url/链接」 ("website/site/URL/link"),
ignoring distance. 「出具报告」 ("issue a report") is brand pain-point copy, and 「有官网但搜索获客一般的企业」 ("companies with a website but weak search acquisition")
is a "suitable customer" option -- two unrelated things half a paragraph apart got ANDed together and classified as a website audit request,
hijacking the whole find-customers flow into the diagnosis flow. **This text is assembled by the product itself** (card options joined with 「、」
on submit), not typed by the user: any selection that includes one option with 「报告」 ("report") and one with 「官网」 ("website")
triggers it.

The second layer is worse: once in the audit flow there was **no exit**. Two state branches each unconditionally `return True`,
so the find-customers branch below is never reached; the only place in the file that clears `detection_flow` is
inside `if detected_url:`. Even typing 「推荐企业?」 ("recommend companies?") couldn't get out -- `_is_detection_entry`
is False for it, so it never reaches the entry check; it is the **state** that intercepts it. So tightening the entry alone isn't enough;
users stuck in existing sessions stay stuck.

Run:
    .venv-test/bin/python -m pytest tests/sales_agent/test_detection_hijack.py -q
"""
from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.sales_agent.api import _is_detection_entry, _run_deterministic_flow
from agents.sales_agent.deps import SalesAgentDeps
from agents.sales_agent.models import (
    SalesAgentChatMessageORM,
    SalesAgentChatSessionORM,
)
from geo.database import Base
from geo.models.user import UserORM

USER = 3300001

# The real production message the user actually sent (assembled from card options)
HIJACK_TEXT = (
    "稿件是否被AI引擎引用、引了谁、带来的访客是否转化、转化金额，均缺乏可对账的第三方数据、"
    "现有的GEO服务商既做优化、又为自己的优化效果出具报告——既当运动员、又当裁判员、"
    "品牌方可申请体验全链路GEO效果计量能力、有官网但搜索获客一般的企业"
)


@pytest.fixture()
def deps():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(UserORM(id=USER, email="hijack@test.local", password_hash="x", is_active=True))
    session = SalesAgentChatSessionORM(user_id=USER, public_id="hijack-1", title="Sales Agent 会话")
    db.add(session)
    db.commit()
    d = SalesAgentDeps(account_id=USER, user_id=USER, db=db, session_id=session.id, session_public_id="hijack-1")
    try:
        yield d
    finally:
        db.close()


def _run(deps, message: str, intent: str = "lead") -> bool:
    return asyncio.run(_run_deterministic_flow(deps, message, intent))


def _stuck_in_detection(deps, step: str = "detection_url") -> None:
    """Reproduce a session misrouted into the audit flow, stuck waiting for a URL."""
    deps.tool_state["detection_flow"] = {"step": step}
    row = deps.db.get(SalesAgentChatSessionORM, deps.session_id)
    row.context_json = json.dumps({"detection_flow": {"step": step}}, ensure_ascii=False)
    deps.db.commit()


def _detection_step(deps) -> str:
    return str((deps.tool_state.get("detection_flow") or {}).get("step") or "")


def _replies(deps) -> list[str]:
    return [
        m.content for m in deps.db.query(SalesAgentChatMessageORM)
        .filter(SalesAgentChatMessageORM.session_id == deps.session_id,
                SalesAgentChatMessageORM.role == "assistant").all()
    ]


# --- Entry: long text must not be classified as an audit request ---

def test_option_blob_is_not_a_detection_request():
    """The production message: "report" and "website" half a paragraph apart do not mean a website audit."""
    assert _is_detection_entry(HIJACK_TEXT) is False


def test_short_natural_phrasing_still_matches():
    """Tightening the entry must not also block normal phrasings."""
    for text in ("帮我检测一下官网", "看看我们网站的诊断报告", "官网诊断一下"):
        assert _is_detection_entry(text) is True, text


def test_exact_phrases_ignore_the_length_gate():
    """The exact-phrase tier is whole-string equality anyway, unaffected by the length gate."""
    for text in ("检测官网", "官网检测", "生成诊断报告", "先检测官网"):
        assert _is_detection_entry(text) is True, text


def test_find_customer_entry_is_never_a_detection_request():
    for text in ("推荐企业?", "找客户", "推荐客户", "找企业"):
        assert _is_detection_entry(text) is False, text


def test_option_blob_does_not_start_detection_flow(deps):
    """End to end: sending that whole paragraph must not push the session into the audit flow."""
    _run(deps, HIJACK_TEXT, "lead")
    assert _detection_step(deps) == "", "找客户流程被劫进检测流程了"
    assert not any("要检测的官网" in r for r in _replies(deps))


# --- Exit: once in, must be able to get out ---

def test_find_customer_escapes_a_stuck_detection_flow(deps):
    """Typing 「推荐企业?」 ("recommend companies?") while waiting for a URL must exit, not repeat the same card."""
    _stuck_in_detection(deps, "detection_url")
    _run(deps, "推荐企业?", "lead")

    assert _detection_step(deps) == "", "检测流程没退出,用户还卡在里面"
    assert not any("要检测的官网" in r for r in _replies(deps)), "又复读了一遍官网卡"


def test_escape_from_keywords_step_does_not_burn_a_report(deps):
    """The keyword-waiting step matters most -- without exiting it starts the diagnosis immediately.

    That step is an unconditional `_append_detection_report_started`: 8-20 minutes per run, 4000 credits.
    The user is clearly saying "recommend companies"; that sentence must not be taken as a brand term to start a diagnosis.
    """
    _stuck_in_detection(deps, "detection_keywords")
    _run(deps, "推荐企业?", "lead")

    assert _detection_step(deps) == ""
    assert not any("诊断报告" in r for r in _replies(deps)), "拿找客户的话起了一单诊断"


def test_url_still_advances_the_detection_flow(deps):
    """The exit must not let real audit flows escape: given a URL, proceed as usual."""
    _stuck_in_detection(deps, "detection_url")
    _run(deps, "https://example.com", "chat")

    assert _detection_step(deps) == "detection_keywords"


def test_unrelated_message_still_gets_the_url_card(deps):
    """Messages that don't match the exit criteria behave as before -- still prompt for the URL, not a blanket pass."""
    _stuck_in_detection(deps, "detection_url")
    _run(deps, "嗯", "chat")

    assert _detection_step(deps) == "detection_url"
    assert any("要检测的官网" in r for r in _replies(deps))
