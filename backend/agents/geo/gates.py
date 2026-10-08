"""Capability gates — the **only criterion** for "can this channel do this".

The original three switches (`AGENT_GATE_PROMPT_CONFIG` / `AGENT_ALLOW_EXTERNAL_PUBLISH` /
`AGENT_ALLOW_PAID_PUBLISH`) were global bools: turning one on opened it for every channel. But risk differs between channels
by orders of magnitude — a user clicking in our own web app versus a host assistant in a skill marketplace calling on their behalf
differs not in capability but in **who is speaking**: anyone in an IM group can send messages, while the account is bound to the installer.

So it was upgraded to a "capability × channel" matrix:

    Global hard switch (emergency shutdown) -> channel allowlist -> embed token caps -> call_policy action allowlist

If any of the four layers says no, it's no. The global switch can only **tighten**; it can't open a channel not on the channel allowlist.

Paired with two layers of guarantees, both required (following the existing `_ADMIN_PAID` approach):
  · **Load time**: `_line_tools` decides by channel whether to load the schema. If it can't see it, it won't call it,
    nor say things like "I could publish it for you but I'm blocked" that invite follow-ups.
  · **Run time**: the first line of every money-spending tool body is `require()`. If load time misses, run time still blocks.
"""
from __future__ import annotations

import os
from typing import Optional

# Capability name -> (env name, channels allowed by default)
CAPS: dict[str, tuple[str, tuple[str, ...]]] = {
    # Persisting seed terms / queries to DB. Previously blocked by the sales gate by default, so the self-service loop got stuck at step two.
    "prompt_config": ("AGENT_CAP_PROMPT_CONFIG_CHANNELS", ("web", "admin")),
    # Publish to own site / own accounts
    "publish_own": ("AGENT_CAP_PUBLISH_OWN_CHANNELS", ("web", "admin")),
    # Paid media publishing — real money and irreversible
    "publish_paid": ("AGENT_CAP_PUBLISH_PAID_CHANNELS", ("web", "admin")),
    # Touches the user's wallet (drafting, sentiment monitoring and other charged actions). **Ops is not on the list** —
    # when ops acts on a customer's behalf it shouldn't spend the customer's wallet; that path goes through the web admin.
    "wallet_spend": ("AGENT_CAP_WALLET_SPEND_CHANNELS", ("web",)),
}

# Global hard switch: for **emergency shutdown**, on by default. In an incident, set it to 0 to shut off all channels of a capability at once,
# without code changes or recomputing the channel allowlist.
#
# Flipping the default from 0 to 1 is deliberate: what really blocks is the channel matrix above + spend.py's caps,
# not these two bools. Leaving them at 0 only makes "can users publish themselves" depend on whether someone remembered to set env,
# and the production backend/.env has no AGENT_* at all.
_KILL_SWITCH: dict[str, str] = {
    "publish_own": "AGENT_ALLOW_EXTERNAL_PUBLISH",
    "publish_paid": "AGENT_ALLOW_PAID_PUBLISH",
}
_KILL_DEFAULT = "1"

# External channels: embed token / IM / skill marketplace. These can never reach money-spending and media-writing tools,
# **regardless of env** — env can only tighten, never loosen (same nature as `build_embed_agent`'s
# `if line == "admin": line = "geo"`: a mechanism gate, not a config gate).
EXTERNAL_CHANNELS = ("embed", "im", "skill", "api")


def _truthy(v: Optional[str]) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes")


def _channels(cap: str) -> tuple[str, ...]:
    env_name, default = CAPS[cap]
    raw = (os.environ.get(env_name) or "").strip()
    if not raw:
        return default
    return tuple(x.strip() for x in raw.split(",") if x.strip())


def channel_allows(cap: str, channel: str) -> bool:
    """Pure channel check — used at load time (when there are no deps yet)."""
    if cap not in CAPS:
        return False
    if channel in EXTERNAL_CHANNELS:
        return False                      # Mechanism gate, not configurable
    kill = _KILL_SWITCH.get(cap)
    if kill is not None and not _truthy(os.environ.get(kill, _KILL_DEFAULT)):
        return False
    return (channel or "web") in _channels(cap)


def allow(deps, cap: str) -> bool:
    """Run-time check: channel + token caps."""
    if not channel_allows(cap, getattr(deps, "channel", "web")):
        return False
    caps = list(getattr(deps, "caps", None) or [])
    if "write" not in caps:
        return False                      # Read-only tokens can't do any write action
    return True


def require(deps, cap: str) -> Optional[dict]:
    """On a hit, return a structured blocked dict; the caller returns it as-is.

    **Return a dict rather than raise**: pydantic-ai's retries=2 would feed the exception back for the model to retry,
    and insufficient permission is the same no matter how many retries — it just burns two rounds of tokens and produces a more confused reply.
    """
    if allow(deps, cap):
        return None
    return {
        "blocked": True,
        "modified": False,
        "capability": cap,
        "note": _BLOCKED_NOTE.get(cap, "当前通道没有这个权限,请在网页端操作。"),
    }


_BLOCKED_NOTE = {
    "prompt_config": "当前通道不能改提示词。请到网页的主题设置里改,或换成在自家网页里问我。",
    "publish_own": "当前通道不能发布内容。请到网页的发布中心操作。",
    "publish_paid": "当前通道不能投放付费媒体 —— 那是真实扣款且不可撤销的动作,"
                    "只在自家网页里开放。请到网页的发布中心操作。",
    "wallet_spend": "当前通道不能动用账户余额。请到网页里操作。",
}
