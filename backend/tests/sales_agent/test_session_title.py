"""Session title.

It used to consider only the first user message -- a session starting with "hello?" was forever named hello?,
and in production a screen of a dozen-plus sessions were all hello?, impossible to tell apart.

Run:
    venv/bin/python -m pytest tests/sales_agent/test_session_title.py -q
"""
from __future__ import annotations

import pytest

from agents.sales_agent.methods import _DEFAULT_SESSION_TITLE, _should_retitle


@pytest.mark.parametrize("title,content,expected", [
    (_DEFAULT_SESSION_TITLE, "帮我找客户", True),        # default title + content -> replace
    ("hello?", "帮我找客户", True),                       # greeting title + content -> replace
    ("你好", "帮我推荐 50 家工业制造企业", True),
    (_DEFAULT_SESSION_TITLE, "hello?", False),           # a greeting itself isn't worthy of a title
    ("hello?", "在吗", False),                            # still a greeting, don't replace
    ("帮我找客户", "再找 50 家", False),                  # already settled, not changed later
    ("", "", False),
    ("", "   ", False),
])
def test_retitle_rules(title, content, expected):
    assert _should_retitle(title, content) is expected


def test_meaningful_title_is_stable():
    """Once the title is settled it no longer follows along -- otherwise the session name keeps jumping and is even harder to recognize."""
    assert not _should_retitle("帮我找长三角的客户", "那先看看报告")
