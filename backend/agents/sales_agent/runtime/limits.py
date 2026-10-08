"""Timeouts and usage caps.

The original gap: across the whole codebase only outbound httpx / SMTP had timeouts; model calls and whole agent run turns
had no cap at all — once the provider hung, the request hung along with it until the gateway timed out, and users saw a spinner.

Round and token caps use Pydantic AI's built-in UsageLimits directly, not our own implementation.
"""
from __future__ import annotations

import asyncio
import os
from typing import Awaitable, TypeVar

T = TypeVar("T")

# Scene -> whole-turn wall-clock timeout (seconds). lead runs the recommendation orchestration, so it gets more room.
_DEFAULT_TIMEOUTS = {"chat": 60, "lead": 180, "pro": 120}


def timeout_for(scene: str) -> int:
    """Overridable via env: SALES_AGENT_TIMEOUT_CHAT / _LEAD / _PRO."""
    key = f"SALES_AGENT_TIMEOUT_{scene.upper()}"
    raw = os.environ.get(key, "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return _DEFAULT_TIMEOUTS.get(scene, _DEFAULT_TIMEOUTS["chat"])


def usage_limits_for(scene: str):
    """Max model requests / tool calls / tokens per turn.

    request_limit is the hard gate against infinite loops: when the model keeps calling tools without converging, the framework raises
    UsageLimitExceeded to terminate, rather than burning on forever.
    """
    from pydantic_ai.usage import UsageLimits

    if scene == "lead":
        return UsageLimits(request_limit=10, tool_calls_limit=14, total_tokens_limit=200_000)
    return UsageLimits(request_limit=6, tool_calls_limit=8, total_tokens_limit=120_000)


class SalesAgentTimeout(Exception):
    """Whole-turn timeout; the caller converts it to user-readable copy, not passed through to the frontend."""


async def run_guarded(awaitable: Awaitable[T], *, scene: str) -> T:
    """Wrap one agent run turn in a wall-clock timeout. Raises SalesAgentTimeout on timeout."""
    seconds = timeout_for(scene)
    try:
        return await asyncio.wait_for(awaitable, timeout=seconds)
    except asyncio.TimeoutError as e:
        raise SalesAgentTimeout(f"{scene} 场景超过 {seconds}s 未返回") from e


def public_message(exc: BaseException) -> str:
    """Translate timeout/over-limit into a single user-facing sentence."""
    name = type(exc).__name__
    if isinstance(exc, SalesAgentTimeout):
        return "这次处理时间偏长,已经先停下来了。可以把问题说得更具体一点再试一次。"
    if "UsageLimit" in name:
        return "这一轮尝试的步骤有点多,已经先停下来了。可以把需求拆成两步再说一次。"
    return ""
