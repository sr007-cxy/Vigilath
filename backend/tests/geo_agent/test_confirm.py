"""Mechanism tests for two-step confirmation tickets -- pure functions, no LLM, no DB, must run in CI.

confirm.py is the security fulcrum of the whole ops console write path: without it, under end_strategy="exhaustive"
DeepSeek could perfectly well "preview -> immediately self-confirm" within one response, and two-step confirmation would be worth nothing.
This file pins every gate.

Run:
    venv/bin/python -m pytest tests/geo_agent/test_confirm.py -q   # cwd = backend/
"""
from __future__ import annotations

import time

import pytest

from agents.geo import confirm as C
from agents.geo.deps import AgentDeps


def _deps(*, turn="turn-A", account=7, topic=42, msg="确认") -> AgentDeps:
    return AgentDeps(account_id=account, user_id=account, db=None,
                     scope="admin", locked_topic_id=topic, turn_id=turn, user_message=msg)


ARGS = {"force": False, "days": 7}


def _issue(deps, tool="admin_confirm_plan", args=None):
    return C.issue(deps, tool=tool, args=args if args is not None else ARGS)[0]


# ── Happy path ────────────────────────────────────────────────────────────────
def test_cross_turn_passes():
    """Preview issued in turn A, user confirms in turn B -> allowed."""
    token = _issue(_deps(turn="turn-A"))
    C.verify(_deps(turn="turn-B"), token, tool="admin_confirm_plan", args=ARGS)


def test_guard_returns_none_when_ok():
    token = _issue(_deps(turn="turn-A"))
    assert C.guard(_deps(turn="turn-B"), token, tool="admin_confirm_plan", args=ARGS) is None


# ── Gate 1: same-turn self-confirmation (the fulcrum of the whole design) ─────────────────────────────────
def test_same_turn_rejected():
    d = _deps(turn="turn-A")
    token = _issue(d)
    with pytest.raises(C.ConfirmError) as e:
        C.verify(d, token, tool="admin_confirm_plan", args=ARGS)
    assert e.value.code == "SAME_TURN"


def test_same_turn_via_guard_returns_dict_not_raise():
    """Failure **must not** raise ModelRetry -- retries=2 would give the model two chances to guess a token."""
    d = _deps(turn="turn-A")
    out = C.guard(d, _issue(d), tool="admin_confirm_plan", args=ARGS)
    assert isinstance(out, dict)
    assert out["code"] == "SAME_TURN"
    assert out["ok"] is False and out["modified"] is False


# ── Gate 2: parameter fingerprint ──────────────────────────────────────────────────────
def test_changed_args_rejected():
    """Swaps like "preview 3 articles, confirm 30" must be blocked."""
    token = _issue(_deps(turn="turn-A"), args={"limit": 3})
    with pytest.raises(C.ConfirmError) as e:
        C.verify(_deps(turn="turn-B"), token, tool="admin_confirm_plan", args={"limit": 30})
    assert e.value.code == "BAD_TOKEN"


def test_token_field_itself_not_part_of_fingerprint():
    """The fingerprint must exclude confirm_token itself, otherwise the second call (with the token) can never match."""
    d1, d2 = _deps(turn="turn-A"), _deps(turn="turn-B")
    token = _issue(d1, args={**ARGS, "confirm_token": ""})
    C.verify(d2, token, tool="admin_confirm_plan", args={**ARGS, "confirm_token": token})


def test_arg_order_does_not_matter():
    token = _issue(_deps(turn="turn-A"), args={"a": 1, "b": 2})
    C.verify(_deps(turn="turn-B"), token, tool="admin_confirm_plan", args={"b": 2, "a": 1})


# ── Gate 3: cross-tool / cross-project / cross-account ───────────────────────────────────────
def test_cross_tool_rejected():
    token = _issue(_deps(turn="turn-A"), tool="admin_approve_doc")
    with pytest.raises(C.ConfirmError) as e:
        C.verify(_deps(turn="turn-B"), token, tool="admin_confirm_plan", args=ARGS)
    assert e.value.code == "BAD_TOKEN"


def test_cross_topic_rejected():
    """Using project A's token to confirm an action on project B -- the main form of cross-customer miswrites."""
    token = _issue(_deps(turn="turn-A", topic=42))
    with pytest.raises(C.ConfirmError) as e:
        C.verify(_deps(turn="turn-B", topic=99), token, tool="admin_confirm_plan", args=ARGS)
    assert e.value.code == "BAD_TOKEN"


def test_cross_account_rejected():
    token = _issue(_deps(turn="turn-A", account=7))
    with pytest.raises(C.ConfirmError) as e:
        C.verify(_deps(turn="turn-B", account=8), token, tool="admin_confirm_plan", args=ARGS)
    assert e.value.code == "BAD_TOKEN"


# ── Gate 4: format / tampering / expiry ────────────────────────────────────────────
@pytest.mark.parametrize("bad", ["", "确认", "true", "v1.abc", "v2.1.2.3", "v1.1.2"])
def test_malformed_token_rejected(bad):
    """What the model is most likely to do: fill in 「确认」 ("confirm") or true directly as a fake token."""
    with pytest.raises(C.ConfirmError) as e:
        C.verify(_deps(turn="turn-B"), bad, tool="admin_confirm_plan", args=ARGS)
    assert e.value.code == "BAD_TOKEN"


def test_tampered_signature_rejected():
    token = _issue(_deps(turn="turn-A"))
    head, _, sig = token.rpartition(".")
    with pytest.raises(C.ConfirmError) as e:
        C.verify(_deps(turn="turn-B"), f"{head}.{'0' * len(sig)}",
                 tool="admin_confirm_plan", args=ARGS)
    assert e.value.code == "BAD_TOKEN"


def test_expired_token(monkeypatch):
    monkeypatch.setenv("AGENT_CONFIRM_TTL", "60")
    token = _issue(_deps(turn="turn-A"))
    monkeypatch.setattr(time, "time", lambda: 10 ** 10)
    with pytest.raises(C.ConfirmError) as e:
        C.verify(_deps(turn="turn-B"), token, tool="admin_confirm_plan", args=ARGS)
    assert e.value.code == "TOKEN_EXPIRED"


def test_expired_token_auto_reissues_preview(monkeypatch):
    """Expiry doesn't error; a fresh preview is re-issued automatically -- replying "token expired" would stall the conversation."""
    monkeypatch.setenv("AGENT_CONFIRM_TTL", "60")
    token = _issue(_deps(turn="turn-A"))
    monkeypatch.setattr(time, "time", lambda: 10 ** 10)

    d = _deps(turn="turn-B")
    fresh = C.guard(d, token, tool="admin_confirm_plan", args=ARGS,
                    rebuild_preview=lambda: C.needs_confirm(
                        d, tool="admin_confirm_plan", action_label="确认发文表",
                        args=ARGS, preview={"计划行总数": 12}, impact=["会产稿 12 篇"]))
    assert fresh["needs_confirm"] is True
    assert fresh["confirm_token"] and "超时" in fresh["_note"]


# ── Gate 5: negation veto ────────────────────────────────────────────────────
@pytest.mark.parametrize("msg", ["算了", "先别发了", "不用了", "取消", "再想想", "cancel"])
def test_declined_cancels_even_with_valid_token(msg):
    """The token is perfectly valid, but the user changed their mind this turn -- must cancel, not execute."""
    token = _issue(_deps(turn="turn-A"))
    out = C.guard(_deps(turn="turn-B", msg=msg), token, tool="admin_confirm_plan", args=ARGS)
    assert isinstance(out, dict) and out.get("cancelled") is True


@pytest.mark.parametrize("msg", ["确认", "好的,执行吧", "发", "嗯就这样"])
def test_affirmative_not_treated_as_decline(msg):
    token = _issue(_deps(turn="turn-A"))
    assert C.guard(_deps(turn="turn-B", msg=msg), token,
                   tool="admin_confirm_plan", args=ARGS) is None


# ── Preview response shape ──────────────────────────────────────────────────────
def test_needs_confirm_shape():
    d = _deps(turn="turn-A")
    out = C.needs_confirm(d, tool="admin_batch_generate_docs", action_label="批量产稿",
                          args={"days": 7}, preview={"将派发": 12}, impact=["消耗 12 次产稿额度"],
                          irreversible=["已出稿的行会被覆盖"], cost={"currency": "CNY", "amount": "0"})
    assert out["needs_confirm"] is True
    assert out["modified"] is False          # not a single byte of data changed in the preview stage
    assert out["action"] == "admin_batch_generate_docs"
    assert out["confirm_token"] and out["expires_in"] > 0
    for k in ("preview", "impact", "irreversible", "cost", "how_to_confirm"):
        assert k in out


# ── Customer line: which tools must use two-step confirmation ──────────────────────────────────────
#
# Before 2026-08-30 the customer line had **none** -- spend.py said "paid media placement has no second confirmation;
# publish if the balance is enough", relying only on a "quote first" line in each turn's preamble. Product testing concluded bluntly:
# "credit-consuming batch run services run automatically with no notice; credits aren't refunded after service interruptions".
#
# The criterion is on source rather than behavior: stubbing each one needs four fixtures (task / media library / price table / balance),
# while what really needs pinning here is "nobody casually deletes these two lines". Behavior is covered in the respective tests
# (e.g. same-turn self-confirmation and cancellation in test_sentiment_once.py).
SPEND_TOOLS = [
    ("agents.geo.tools", "run_sentiment_once"),       # charged per run
    ("agents.geo.tools", "draft_articles"),           # charged per article, at most 20 at once
    ("agents.geo.tools", "set_selected_queries"),     # persisting starts continuous batch runs
    ("agents.geo.tools", "run_full_diagnosis"),       # consumes a diagnosis slot
    ("agents.geo.tools_monitor", "trigger_batch_run"),        # charged per cell
    ("agents.geo.tools_billing", "start_sentiment_monitor"),  # charged daily from then on
    ("agents.geo.tools_billing", "create_topup_order"),       # touches the user's money
    ("agents.geo.tools_publish", "pay_and_publish"),          # real charge, irreversible
]


@pytest.mark.parametrize("mod_name,fn_name", SPEND_TOOLS)
def test_spend_tools_are_two_step(mod_name, fn_name):
    import importlib
    import inspect

    fn = getattr(importlib.import_module(mod_name), fn_name)
    assert "confirm_token" in inspect.signature(fn).parameters, \
        f"{fn_name} 没有 confirm_token 参数 —— 它会不问就扣"
    src = inspect.getsource(fn)
    assert "needs_confirm(" in src, f"{fn_name} 缺预览"
    assert ".guard(" in src, f"{fn_name} 缺票据校验,预览形同虚设"
