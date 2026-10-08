"""Behavior parity gate for the two chat entry points.

`/chat/a2ui` (the page's main path) and `/chat` (SSE) used to run separately: the former ran the deterministic flow
(audit guidance / profile card / four filter cards / entry guidance), the latter had only a quick_reply short-circuit
and went straight to the model -- the same sentence got two results from two entry points. After convergence both must first go through
`_run_deterministic_flow`; if anyone inlines the flow back into their own function body, this turns red immediately.

Run:
    venv/bin/python -m pytest tests/sales_agent/test_chat_entry_parity.py -q
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from agents.sales_agent.agent import should_use_guide_reply
from agents.sales_agent.runtime.input_guard import identity_or_reference_kind

API = pathlib.Path(__file__).resolve().parents[2] / "agents" / "sales_agent" / "api.py"
TREE = ast.parse(API.read_text(encoding="utf-8"))
FLOW = "_run_deterministic_flow"


def _func(name: str) -> ast.AST:
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"api.py 里找不到 {name}")


def _calls(node: ast.AST) -> set[str]:
    return {n.func.id for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


def _step_branches(node: ast.AST) -> int:
    """Count flow branches like `flow.get("step")` / `.get("step")`."""
    return sum(
        1 for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get"
        and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == "step"
    )


@pytest.mark.parametrize("entry", ["chat_a2ui", "chat"])
def test_both_entries_run_the_shared_flow(entry):
    assert FLOW in _calls(_func(entry)), f"{entry} 没走共用确定性流程"


@pytest.mark.parametrize("entry", ["chat_a2ui", "chat"])
def test_entries_hold_no_flow_branches_of_their_own(entry):
    assert _step_branches(_func(entry)) == 0, f"{entry} 又把流程分支内联回去了"


def test_flow_lives_in_one_place():
    assert _step_branches(_func(FLOW)) > 0, "确定性流程里没有流程分支,大概是被挪走了"


def test_shared_flow_returns_bool_not_response():
    """The flow only reports "handled or not"; each entry point decides its own response shape -- otherwise SSE would have to swallow a JSON bundle again."""
    node = _func(FLOW)
    for n in ast.walk(node):
        if isinstance(n, ast.Return) and isinstance(n.value, ast.Call):
            name = getattr(n.value.func, "id", "") or getattr(n.value.func, "attr", "")
            assert name != "session_bundle", "确定性流程不应直接返回 session_bundle"


def test_lead_step_chain_is_gone():
    """The find-customers part changed from "what was the previous step" to "which slots are still missing" -- only the profile confirm card still branches by step.

    If a chain of per-step ifs grows back, the slot routing has been bypassed.
    """
    # 3 places: the audit flow reads step, the profile confirm card branches by step, slot routing reads step to hand to the current step's parser
    assert _step_branches(_func(FLOW)) <= 3, "找客户流程又长回按步 if 串了,该走 flow/router.next_step"


def test_entry_guidance_does_not_swallow_real_questions():
    """Entry guidance is given only on the first greeting/small talk; later ordinary chat goes to plain_chat.

    Production 2026-08-23: a user asked "which companies have we emailed?" (the DB really had 3 sent emails),
    and got "which step would you like to do first?" -- the message never reached the model, so even a complete set of new tools was useless.

    This used to pin the literal of that long boolean expression, so any refactor of the criterion turned it red, and red meant "the literal changed",
    not "guidance is swallowing questions again". Now it guards two real invariants:
      1. guidance is given only when "no card is pending" and "no assistant message yet this round" -- i.e. only on the first round;
      2. real questions never count as greeting/identity/small talk under the entry criterion, so they never enter this branch.
    """
    src = API.read_text(encoding="utf-8")

    # 1. First turn only. Drop any one gate and the guide pops up mid-conversation, covering the real answer
    i = src.index("_append_entry_guidance(deps, intent)")
    guard = src[src.rindex("if ", 0, i):i]
    assert "not step" in guard, "入口引导没挡住「有卡挂着」的情况"
    assert "not _assistant_message_seen(deps)" in guard, "入口引导没限定在首轮"
    assert "return True" in src[i:i + 120], "给了引导却没把消息标成已处理"

    # 2. Real questions must not be mistaken by the entry checks for greeting/identity/small talk -- the direct cause of the 2026-08-23 incident
    for q in ("都发过哪些企业?", "之前推荐过哪些公司", "昨天发了几封邮件", "推荐 50 家工业制造企业"):
        assert not should_use_guide_reply(q), f"{q!r} 被当成了问候"
        assert not identity_or_reference_kind(q), f"{q!r} 被当成了身份/指代问题"
