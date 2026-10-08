"""Ownership checks on the customer line -- tools that accept ids must check ownership first.

`account_id` is injected by the backend and never a model-fillable parameter (the iron rule in deps.py), but the model can fill
`doc_id` / `task_id` / `item_id`. Without ownership checks on these ids, it's "operating directly with an id from another account
in a previous turn" -- and in conversation the model easily carries numbers over from earlier turns.
"""
from __future__ import annotations

import ast
import inspect
import re

import pytest

from agents.geo import tools, tools_publish

# Tool name -> the ownership helper it must call
_MUST_CHECK = {
    "auto_select_media": "_own_doc",
    "push_media_draft": "_own_doc",
    "submit_publish_url": "_own_task",
    "preview_paid_publish": "_own_task",
    "pay_and_publish": "_own_task",
}


@pytest.mark.parametrize("fn_name,helper", sorted(_MUST_CHECK.items()))
def test_id_taking_tools_check_ownership(fn_name, helper):
    src = inspect.getsource(getattr(tools_publish, fn_name))
    assert re.search(rf'\b{helper}\(', src), f"{fn_name} 没调 {helper} 校归属"


def test_actor_is_a_real_user_row_not_a_fake_object():
    """Endpoints read .email / .name for audit trails; a fake object only has .id, and touching it yields a silent empty string.

    The docstring of `scope.admin_actor` explicitly forbids the shape `type("Actor", (), {...})()`,
    and `convert_action_item` was the one that slipped through -- this assertion keeps it from growing back.
    """
    # Use AST rather than text matching -- comments and docstrings mention this shape precisely to say
    # "don't write it like this again", and text matching would flag the explanation itself as a violation.
    for mod in (tools, tools_publish):
        tree = ast.parse(inspect.getsource(mod))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "id", "") != "type" or not node.args:
                continue
            first = node.args[0]
            assert not (isinstance(first, ast.Constant) and first.value == "Actor"), \
                f"{mod.__name__} 里还有伪 actor 对象(第 {node.lineno} 行)"


def test_self_actor_queries_the_db():
    """Re-query the DB rather than trusting the deps snapshot -- a ban mid-session takes effect immediately."""
    src = inspect.getsource(tools_publish._self_actor)
    assert "db.get(UserORM" in src


def test_spending_tools_refuse_topic_fallback():
    """Money-spending tools don't accept the "latest batch run" soft fallback.

    On read-only tools the soft fallback is a convenience (the user is most likely looking at that topic); on money-spending tools it's
    the direct source of "published a draft to the wrong topic".
    """
    src = inspect.getsource(tools_publish._spend_topic)
    assert "last_run_at" not in src, "花钱路径上出现了「最近跑批」回退"
    assert "ModelRetry" in src, "没主题时应该让模型去问用户,而不是自己挑一个"

    pay = inspect.getsource(tools_publish.pay_and_publish)
    assert "_spend_topic(" in pay


def test_ownership_errors_are_model_retry_not_silent():
    """Ownership mismatch must raise ModelRetry (so the model retries with another id and tells the truth), never silently return empty."""
    for name in ("_own_doc", "_own_task"):
        src = inspect.getsource(getattr(tools_publish, name))
        assert src.count("ModelRetry") >= 2, f"{name} 的失败分支没有全部 ModelRetry"
