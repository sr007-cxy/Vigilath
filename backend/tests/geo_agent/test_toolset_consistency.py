"""Toolset consistency -- prevents "registry and actual loading drifting apart" from happening again.

A real failure before 2026-08-25: `build_agent("both")` used `TOOLS`, and `TOOLS` lacked
the two one-off quick-check tools (while `_line_tools("both")` had them). As a result the internal web agent couldn't reach
quick checks under mixed intent, while the external embed could -- exactly backwards.
"""
from __future__ import annotations

import inspect

import pytest

from agents.geo import agent as A
from agents.geo import tools as T


def _names(items):
    return {getattr(t, "__name__", str(t)) for t in items}


def test_registry_covers_every_line_toolset():
    """TOOLS must be a superset of all customer-line tools. Missing one is the failure above."""
    装载 = _names(A._READ_TOOLS + A._WRITE_TOOLS + [T.publish_drafts])
    缺 = 装载 - _names(T.TOOLS)
    assert not 缺, f"TOOLS 注册表漏了这些已经在装的工具:{缺}"


def test_both_line_reaches_quick_sentiment_check():
    """Mixed intent (both) must reach the one-off quick check -- those were exactly the two missed back then."""
    names = _names(A._line_tools("both", can_write=True, channel="web"))
    assert {"run_sentiment_once", "get_sentiment_once"} <= names


def test_customer_tools_never_take_identity_params():
    """account_id / user_id are **never model-fillable parameters** (the iron rule in deps.py).

    topic_id is allowed -- multiple topics need switching, but those two are identities; if the model could fill them it could escalate privileges.
    """
    bad = []
    for fn in A._READ_TOOLS + A._WRITE_TOOLS:
        params = set(inspect.signature(fn).parameters)
        for p in ("account_id", "user_id"):
            if p in params:
                bad.append(f"{fn.__name__}({p})")
    assert not bad, f"这些工具让模型能填身份参数:{bad}"


@pytest.mark.parametrize("fn", A._GEO_WRITE + A._SENTIMENT_WRITE,
                         ids=lambda f: getattr(f, "__name__", str(f)))
def test_write_tools_declare_themselves_in_docstring(fn):
    """The **first line** of write tools' docstrings must have a 【写】/【动作】 ("write" / "action") marker -- the model relies on it to judge whether this really changes things.

    Only check the first line, not the very start: draft_articles has a ⚠️ in front, a deliberate emphasis this assertion shouldn't force out.
    """
    doc = (fn.__doc__ or "").strip()
    assert doc, f"{fn.__name__} 没有 docstring,模型没法判断该不该调"
    first = doc.splitlines()[0]
    assert ("【写" in first) or ("【动作" in first), \
        f"{fn.__name__} 的 docstring 首行缺【写】/【动作】标记:{first[:40]!r}"


def test_shared_tools_are_on_both_lines():
    """websearch_evidence must be on both lines -- when the main pipeline is down both GEO and sentiment rely on it for evidence."""
    for line in ("geo", "sentiment", "both"):
        names = _names(A._line_tools(line, can_write=True, channel="web"))
        assert "websearch_evidence" in names, f"{line} 线没有降级取证工具"
