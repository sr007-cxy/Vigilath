"""Error-message exit gate: don't squash what users should see, don't let raw text that should be blocked through.

`_public_error_message` previously had zero tests, and it was squashing exactly the config errors users most need to see into
"Sales Agent failed this time, please retry later or ask something else." -- because it blocks by **wording**
(api_key / token / 密钥 / 令牌 / 模型 (key/token/model) ...), and messages like "API Key missing, please complete it on the admin page"
match precisely because they name what to fix. The user gets "please retry later", so they keep
retrying, while what's missing is a Key they could configure themselves in two minutes.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from agents.sales_agent.api import _SAFE_RUNTIME_ERROR, _public_error_message
from agents.sales_agent.service.common import SalesAgentError


SERVICE_DIR = pathlib.Path(__file__).resolve().parents[2] / "agents" / "sales_agent"


# These are really raised in production, copied verbatim from the raise sites. Update them when the wording changes; don't delete.
ACTIONABLE = [
    "Resend 发件箱缺少 API Key。",
    "Resend 邮件发送失败,请检查 API Key、发件域名和收件地址。",
    "不支持的报告来源。只能读取 detection_records 或 full_diagnosis_reports。",
    "读取指定报告时必须提供 source_report_id。",
    "source_report_id 必须是数字字符串。",
]


@pytest.mark.parametrize("message", ACTIONABLE)
def test_hand_written_config_errors_reach_the_user_verbatim(message):
    """Hardcoded messages marked public go out as-is -- all of these contain gate keywords."""
    assert _public_error_message(SalesAgentError(message, public=True)) == message


@pytest.mark.parametrize("message", ACTIONABLE)
def test_the_same_text_is_still_scrubbed_without_the_flag(message):
    """The inverse check that the gate still works: the same message not marked public still gets squashed.

    This pairs with the one above -- with only that one, deleting the gate entirely would still be green.
    """
    assert _public_error_message(SalesAgentError(message)) == _SAFE_RUNTIME_ERROR


LEAKY = [
    # outreach.py:1722 dumps the entire context into the message via json.dumps
    '请按 context 生成 content 后重新保存。context={"lead_id": 7, "brand": "某某"}',
    # Model/vendor names and key wording
    "openai 调用失败:Incorrect API key provided sk-proj-xxxx",
    "调用 deepseek 时令牌已过期",
    "Traceback (most recent call last): ...",
    "读取 detection_records 时数据库连接超时",
    "模型返回了无法解析的内容",
]


@pytest.mark.parametrize("message", LEAKY)
def test_foreign_text_is_still_scrubbed(message):
    """Anything with external raw text is squashed to the fallback -- the gate's core job; don't loosen it while allowing things through."""
    assert _public_error_message(SalesAgentError(message)) == _SAFE_RUNTIME_ERROR


def test_known_limit_the_guard_only_knows_the_words_on_its_list():
    """Record this gate's known limits so nobody later assumes it is general-purpose redaction.

    It matches a hardcoded word list (model/openai/api_key/token/密钥/令牌 (key/token)...); vendor error codes
    aren't in the list and go out as-is. This is not a bug: SMS send failures go through
    `delivery.failure_reason` persisted to DB and never pass this exit. Tightening it properly means judging by source,
    which is a separate matter -- until then, don't assume "passed this gate means safe".
    """
    code = "阿里云拒绝下发:isv.SMS_SIGNATURE_ILLEGAL"
    assert _public_error_message(SalesAgentError(code)) == code


def test_public_does_not_buy_unlimited_length():
    """Everyone must pass the length check: even public messages shouldn't dump a wall of text on the user."""
    long_msg = "配置有误,请到管理页补全。" * 40
    assert len(long_msg) > 240
    assert _public_error_message(SalesAgentError(long_msg, public=True)) == _SAFE_RUNTIME_ERROR


def test_empty_message_falls_back():
    assert _public_error_message(SalesAgentError("")) == _SAFE_RUNTIME_ERROR
    assert _public_error_message(None) == _SAFE_RUNTIME_ERROR


def test_caller_supplied_fallback_wins_over_the_generic_one():
    """The chat exit passes "rephrase and ask again" style fallbacks; they must not degrade to a generic failure."""
    assert _public_error_message(SalesAgentError("qwen 返回异常"), "我没太明白,换个说法?") == "我没太明白,换个说法?"


def test_public_is_opt_in():
    """The default must be False -- if it defaulted to allow, any new raise site wrapping external text would leak."""
    assert SalesAgentError("x").public is False


# The body must not contain another `SalesAgentError(` -- otherwise `.*?` spans from the **previous** (possibly far-away)
# raise site all the way to this `public=True`, implicating every non-public raise site in between as interpolation violations.
# With this rule, the match always starts at the call immediately adjacent to `public=True`, which is the one to check.
# (2026-09-13: when public=True first appeared in api.py, dozens of earlier non-public raise sites in the file
#  were all flagged as violations, caused by exactly this cross-call match.)
_PUBLIC_RAISE = re.compile(
    r"SalesAgentError\(\s*(?P<body>(?:(?!SalesAgentError\().)*?)public=True", re.S
)


def test_nothing_marked_public_interpolates_anything():
    """Messages marked public must be pure literals, no f-string interpolation.

    This is the only weak point of the whole allow mechanism: `public=True` is a human judgment that "we wrote this text ourselves",
    and once someone marks `f"...{e}"` public, the gate is effectively disabled for that path. Interpolation means
    external content is coming in, which should go through the word-list scrub. So this pins the source shape -- what it guards is
    source shape anyway, not runtime behavior.
    """
    offenders: list[str] = []
    for path in sorted(SERVICE_DIR.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for m in _PUBLIC_RAISE.finditer(text):
            body = m.group("body")
            if "{" in body or 'f"' in body or "f'" in body:
                line = text[: m.start()].count("\n") + 1
                offenders.append(f"{path.name}:{line} {body.strip()[:70]}")
    assert not offenders, "public=True 的消息里有插值,外部内容会绕过词表闸:\n" + "\n".join(offenders)


def test_the_actionable_messages_are_actually_marked_in_the_source():
    """The ACTIONABLE list above can't be wishful thinking in tests -- it only counts if the raise sites are actually marked."""
    blob = "\n".join(p.read_text(encoding="utf-8") for p in SERVICE_DIR.rglob("*.py"))
    for message in ACTIONABLE:
        i = blob.find(f'"{message}"')
        assert i >= 0, f"抛出点找不到这句话了:{message}"
        assert "public=True" in blob[i:i + 160], f"这句还没标 public,用户看不到:{message}"
