"""Capability gates -- "can this channel do this".

Two layers of guarantee, both required:
  - load time (_line_tools loads schemas per channel) -- what can't be seen won't be called
  - run time (gates.require on the first line of every money-spending tool body) -- still blocks if load time missed it

The second can't rely on human review alone, so below there's a **source scan** assertion.
"""
from __future__ import annotations

import inspect
import re

import pytest

from agents.geo import gates, tools_publish


@pytest.mark.parametrize("cap", sorted(gates.CAPS))
@pytest.mark.parametrize("channel", ["embed", "im", "skill", "api"])
def test_external_channels_denied_for_every_cap(cap, channel):
    assert gates.channel_allows(cap, channel) is False


def test_web_is_the_full_self_service_channel():
    """All four capabilities enabled on our own web page -- otherwise "users can complete the whole flow on their own" is empty talk."""
    for cap in gates.CAPS:
        assert gates.channel_allows(cap, "web") is True, cap


def test_admin_cannot_spend_the_customers_wallet():
    """Ops acting for customers shouldn't spend the customer's wallet -- that path goes through the web admin."""
    assert gates.channel_allows("wallet_spend", "admin") is False
    assert gates.channel_allows("publish_paid", "admin") is True


def test_kill_switch_closes_all_channels(monkeypatch):
    monkeypatch.setenv("AGENT_ALLOW_PAID_PUBLISH", "0")
    for ch in ("web", "admin"):
        assert gates.channel_allows("publish_paid", ch) is False
    # Only this one is turned off; other capabilities are unaffected
    assert gates.channel_allows("prompt_config", "web") is True


def test_env_cannot_open_external_channels(monkeypatch):
    """A misconfiguration shouldn't become a security incident -- external channels are mechanism-gated; env can only tighten."""
    for env_name, _ in gates.CAPS.values():
        monkeypatch.setenv(env_name, "web,admin,embed,im,skill,api")
    monkeypatch.setenv("AGENT_ALLOW_PAID_PUBLISH", "1")
    monkeypatch.setenv("AGENT_ALLOW_EXTERNAL_PUBLISH", "1")
    for ch in gates.EXTERNAL_CHANNELS:
        for cap in gates.CAPS:
            assert gates.channel_allows(cap, ch) is False, f"{cap}@{ch} 被 env 放开了"


def test_require_returns_structured_not_raises():
    """Insufficient permission **must not raise** -- pydantic-ai's retries=2 would make the model retry,
    and a permission problem stays the same however many retries; it only burns two rounds of tokens and spits out something more confused."""
    class _D:
        channel = "im"
        caps = ["read", "write"]

    out = gates.require(_D(), "publish_paid")
    assert out is not None
    assert out["blocked"] is True and out["modified"] is False
    assert "网页" in out["note"]


def test_readonly_token_cannot_write():
    class _D:
        channel = "web"
        caps = ["read"]

    assert gates.allow(_D(), "publish_own") is False


# Every money-spending / media-writing tool must name its own capability in the function body
_MUST_GUARD = {
    "auto_select_media": "publish_own",
    "push_media_draft": "publish_own",
    "submit_publish_url": "publish_own",
}


@pytest.mark.parametrize("fn_name,cap", sorted(_MUST_GUARD.items()))
def test_write_tools_call_the_gate_with_their_own_name(fn_name, cap):
    src = inspect.getsource(getattr(tools_publish, fn_name))
    assert re.search(rf'gates\.require\(\s*ctx\.deps\s*,\s*"{cap}"', src), \
        f"{fn_name} 没有在函数体里调 gates.require(ctx.deps, {cap!r})"


def test_pay_and_publish_goes_through_spend_guard():
    """Paid orders go through spend.precheck rather than gates.require -- the former calls gates first thing,
    plus four more caps. This asserts it really takes that stricter path rather than skipping the gate."""
    src = inspect.getsource(tools_publish.pay_and_publish)
    assert "spend.precheck(" in src
    assert "spend.cooled_down(" in src, "缺冷静期:模型重试会真的下第二单"


def test_pay_and_publish_only_takes_task_ids():
    """A structural defense against prompt injection: neither amount nor media is supplied by the model.

    The task must first be created by a tool under this account's topic; injected text can't forge a valid task_id;
    the amount is always decided by live server-side price verification. Once amount / price appears in a signature, this defense is broken.

    `confirm_token` is an allowlisted exception: it's an HMAC ticket the model can't forge (forging gets BAD_TOKEN),
    and its effect points the same way as this defense -- one more "the user saw the amount with their own eyes".
    """
    sig = inspect.signature(tools_publish.pay_and_publish)
    params = set(sig.parameters) - {"ctx", "confirm_token"}
    assert params == {"task_ids"}, f"多出了不该由模型给的参数:{params - {'task_ids'}}"


def test_paid_publish_requires_two_step_confirm():
    """Paid placement must use two-step confirmation -- on 2026-08-30 the old "publish if the balance is enough" decision was overturned.

    The criterion is on source rather than behavior: behavior tests would need stubs for task / media library / balance,
    while what really needs pinning here is "nobody casually deletes these two lines".
    """
    src = inspect.getsource(tools_publish.pay_and_publish)
    assert "confirm.needs_confirm(" in src, "缺预览:会变回不问就扣"
    assert "confirm.guard(" in src, "缺校验:预览形同虚设"
