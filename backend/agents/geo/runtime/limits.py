"""Whole-turn timeouts and usage caps.

The original gap: all 9 agent call sites (3 in api.py + 6 in embed/) had neither usage_limits=
nor a wall-clock cap. Once the provider hung, SSE hung until nginx's proxy_read_timeout of 300s,
and users saw a spinner; the IM ones had placeholder cards as cover, but the background tasks also hung and held connections.

Round and token caps use Pydantic AI's built-in UsageLimits directly, not our own implementation.
"""
from __future__ import annotations

import asyncio
import os
from typing import Awaitable, TypeVar

T = TypeVar("T")

# ★ These numbers are **deliberately generous**, and they're not a performance budget.
#
#   When they were set, prod had no agent-side latency distribution to consult (llm_call_log had
#   not a single service='agent' row — that accounting never ran in prod), so any "precise"
#   number would be a guess. Rather than guess a tight one that cuts off normal long tasks, let the cap serve only one purpose:
#
#       wrap up before nginx's proxy_read_timeout of 300s, turning a 504 into a human-readable sentence.
#
#   Tighten once the real distribution is available; until then, better let it be nominal — the cost of a fallback gate
#   hitting legitimate work is greater than it occasionally failing to catch something.
#   Tiers: chat customer line / admin ops console (many tools, frequent chained calls) / pro heavy work / im has a placeholder card covering the ack.
_DEFAULT_TIMEOUTS = {"chat": 240, "admin": 260, "pro": 280, "im": 200}


def profile_for(scene: str = "chat", line: str = "", channel: str = "") -> str:
    """Collapse (scene, line, channel) into a tier name."""
    if scene == "pro":
        return "pro"
    if line == "admin":
        return "admin"
    if channel == "im":
        return "im"
    return "chat"


def timeout_for(profile: str) -> int:
    """Whole-turn wall-clock timeout (seconds). env `AGENT_TIMEOUT_{CHAT,ADMIN,PRO,IM}`; <=0 means unlimited."""
    raw = os.environ.get(f"AGENT_TIMEOUT_{profile.upper()}", "").strip()
    if raw.lstrip("-").isdigit():
        return int(raw)
    return _DEFAULT_TIMEOUTS.get(profile, _DEFAULT_TIMEOUTS["chat"])


def usage_limits_enabled() -> bool:
    """`AGENT_USAGE_LIMITS=0` turns off round/token caps entirely, reverting to pre-change behavior."""
    return os.environ.get("AGENT_USAGE_LIMITS", "").strip() != "0"


def usage_limits_for(profile: str):
    """Max model requests / tool calls / tokens per turn. Returns None when disabled.

    ★ The admin tier **never sets tool_calls_limit**. The agent runs with end_strategy="exhaustive"
      (fixing DeepSeek skipping tools when it emits text+tool_call in one response), so the framework must run every
      tool_call in a response before End; a tool_calls_limit hit mid-batch raises UsageLimitExceeded,
      **with the first tools executed and the rest not**. For admin_approve_doc (send email + create placement task) and
      admin_batch_generate_docs (dispatches dozens of articles) that means "half executed, return body is an exception".
      Any value may land right in the middle, so the answer is not to raise it but to not set it.
      request_limit is checked before the **next** model request, never splitting a batch of tool_calls,
      and is enough to prevent infinite loops.
    """
    if not usage_limits_enabled():
        return None
    from pydantic_ai.usage import UsageLimits

    # Also deliberately generous: this is a fallback against **runaways** (the model calling tools repeatedly without converging), not a step budget.
    # A complex ops console operation taking a dozen round trips is normal; set it tight and ops are the first to hit it.
    if profile == "admin":
        return UsageLimits(request_limit=20, total_tokens_limit=400_000)
    if profile == "pro":
        return UsageLimits(request_limit=10, tool_calls_limit=24, total_tokens_limit=400_000)
    return UsageLimits(request_limit=15, tool_calls_limit=30, total_tokens_limit=300_000)


class GeoAgentTimeout(Exception):
    """Whole-turn timeout. The caller converts it to user-readable copy without passing seconds or tier names to the user."""


async def run_guarded(awaitable: Awaitable[T], *, profile: str) -> T:
    """Wrap one agent.run() turn in a wall-clock timeout. Raises GeoAgentTimeout on timeout.

    Can't wrap an async generator — the SSE path (agent.iter in api.py::chat) must compare against the deadline
    itself in the node loop, see deadline_for().
    """
    seconds = timeout_for(profile)
    if seconds <= 0:
        return await awaitable
    try:
        return await asyncio.wait_for(awaitable, timeout=seconds)
    except asyncio.TimeoutError as e:
        raise GeoAgentTimeout(f"{profile} 档超过 {seconds}s 未返回") from e


def deadline_for(profile: str) -> float:
    """For SSE: returns a perf_counter time point; <=0 means unlimited (returns +inf).

    prod is Python 3.10, which lacks the asyncio.timeout context manager, so compare manually.
    """
    import time

    seconds = timeout_for(profile)
    return float("inf") if seconds <= 0 else time.perf_counter() + seconds


def public_message(exc: BaseException) -> str:
    """Translate timeout / over-limit / gate-blocked into a single user-facing sentence. Returns an empty string for anything else.

    Never echo seconds, tier names or exception class names — those are internal details users can't understand and shouldn't see.

    ★ The gate (CallDenied) must be translated here: external channels (skill / embed / IM) are bound by daily counts and
      RPM, and hitting them is **normal**, not a fault. It used to run raw all the way to the SSE error event,
      and the skill client called _die and printed a red line — users saw "an error" rather than "you've hit the quota,
      come back tomorrow / use the web version". A wall that isn't translated into human words feels no different from an outage.
    """
    if isinstance(exc, GeoAgentTimeout):
        return "这次处理时间偏长,已经先停下来了。可以把问题说得更具体一点再问一次。"
    if "UsageLimit" in type(exc).__name__:
        return "这一轮要做的步骤有点多,已经先停下来了。可以把需求拆成两步再说一次。"
    # Judge by class name so the runtime layer doesn't depend back on geo.services (same approach as UsageLimit above)
    if type(exc).__name__ == "CallDenied":
        code = int(getattr(exc, "code", 0) or 0)
        if code == 429:
            return ("这个动作的调用次数暂时到上限了 —— 对外通道有每分钟和每天的额度。"
                    "过几分钟再试;要连续跑就到网页版,网页版不受这道限制。")
        if code == 403:
            # ★ This sentence only lists capabilities the agent **really has tools for**.
            #   The original read "site health check, AEO audit, industry leaderboard, brand mentions", three of which
            #   have no corresponding agent tool at all (the site health check was retired long ago; AEO/brand mentions exist only
            #   inside the full diagnosis). Users asking per that sentence left the model unable to reach tools, while forbidden to say
            #   "I can't reach it", so it could only invent a platform step — the error copy itself manufactured a round of hallucination.
            #   Remember to come back here when adding capabilities; don't let it drift again.
            return ("当前授权里没有开放这个动作。不用额外开通就能做的有:查行业榜、"
                    "看报告中心数据、看命中清单;其余能力要在网页版开通后再用。")
        if code == 503:
            return "这个能力临时停用了,过一会儿再试。"
        return "这次调用被平台的调用策略拦下了,过一会儿再试。"
    return ""
