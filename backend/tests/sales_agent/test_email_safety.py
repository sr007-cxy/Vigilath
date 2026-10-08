"""Email safety and sender policy -- the last pure-function gate before real outbound sends.

HTML bodies go straight into customer inboxes: any leaked script/style/iframe/event attribute/javascript: link
turns the user's sending domain into a carrier. A wrong normalization of the send scheduling policy sends mail from the wrong mailbox.

Run:
    venv/bin/python -m pytest tests/sales_agent/test_email_safety.py -q
"""
from __future__ import annotations

import pytest

from agents.sales_agent.service import (
    SalesAgentError,
    normalize_sender_selection_policy,
)
from agents.sales_agent.service.email_send import (
    _sales_agent_email_html,
    _sales_agent_email_html_from_safe_html,
    _sales_agent_email_tracking_pixel,
)

PIXEL = "https://www.vigilath.cn/api/sales-agent/email/open/tok.png"


# --- Safe HTML: dangerous tags must be stripped ---

@pytest.mark.parametrize("tag", ["script", "style", "iframe", "object", "embed", "form", "input", "button"])
def test_dangerous_tags_stripped(tag):
    out = _sales_agent_email_html_from_safe_html(f"<p>正文</p><{tag}>危险</{tag}>")
    assert f"<{tag}" not in out.lower()
    assert "危险" not in out
    assert "正文" in out


def test_unclosed_dangerous_tag_stripped():
    out = _sales_agent_email_html_from_safe_html('<p>正文</p><iframe src="https://evil.com">')
    assert "<iframe" not in out.lower()


def test_event_attributes_stripped():
    out = _sales_agent_email_html_from_safe_html('<p onclick="steal()">正文</p>')
    assert "onclick" not in out.lower()
    assert "正文" in out


def test_javascript_href_neutralized():
    out = _sales_agent_email_html_from_safe_html('<a href="javascript:steal()">点这里</a>')
    assert "javascript:" not in out.lower()
    assert 'href="#"' in out


def test_safe_tags_preserved():
    out = _sales_agent_email_html_from_safe_html('<p><strong>重点</strong>见 <a href="https://a.com/r">报告</a></p>')
    assert "<strong>重点</strong>" in out
    assert '<a href="https://a.com/r">报告</a>' in out


# --- Plain text to HTML: must escape; user text must not become tags ---

def test_plain_text_is_escaped():
    out = _sales_agent_email_html("<b>不是标签</b>")
    assert "&lt;b&gt;" in out
    assert "<b>" not in out


def test_plain_text_urls_linkified():
    out = _sales_agent_email_html("报告见 https://a.com/r/1 谢谢")
    assert '<a href="https://a.com/r/1"' in out
    assert 'rel="noopener noreferrer"' in out


def test_plain_text_newlines_become_br():
    assert "<br />" in _sales_agent_email_html("第一行\n第二行")


# --- Tracking pixel ---

def test_pixel_absent_when_tracking_off():
    assert _sales_agent_email_tracking_pixel("") == ""
    assert "<img" not in _sales_agent_email_html("正文", "")
    assert "<img" not in _sales_agent_email_html_from_safe_html("<p>正文</p>", "")


def test_pixel_present_when_tracking_on():
    assert "<img" in _sales_agent_email_html("正文", PIXEL)
    assert PIXEL in _sales_agent_email_html_from_safe_html("<p>正文</p>", PIXEL)


# --- Send scheduling policy ---

@pytest.mark.parametrize("raw,expected", [
    ("default_only", "default_only"),
    ("", "default_only"),
    (None, "default_only"),
    ("default", "default_only"),
    ("default_account", "default_only"),
    ("default_first", "default_only"),
    ("DEFAULT_ONLY", "default_only"),
    ("round_robin", "round_robin"),
    ("round-robin", "round_robin"),
    ("roundrobin", "round_robin"),
    ("rotation", "round_robin"),
    ("rotate", "round_robin"),
])
def test_sender_policy_normalization(raw, expected):
    assert normalize_sender_selection_policy(raw) == expected


@pytest.mark.parametrize("raw", ["broadcast", "random", "群发"])
def test_sender_policy_rejects_unknown(raw):
    with pytest.raises(SalesAgentError):
        normalize_sender_selection_policy(raw)
