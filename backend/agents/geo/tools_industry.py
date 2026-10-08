"""Industry leaderboard tool — login-free, zero-cost industry-level data.

Why this one was added separately: the 403 copy in `runtime/limits.py` kept telling users "things you can run without authorization
include: site health check, AEO audit, **industry leaderboard**, brand mentions", while the agent side had no matching tool at all.
Meanwhile `agent.py` forbids the model from saying "I can't reach that tool". Squeezed between the two rules, the model's only way out
was to make things up — a user asked about "industrial cleaning robot industry trends", and it substituted data from some brand within reach,
answering convincingly with something that wasn't industry data at all.

So here `geo.api.industry_library` is wrapped as a tool. It's **read-only, free and unlimited**,
with no reason not to provide it.

Two hard rules are written into the return body (the model reads the return body, not this comment):
  1. Industry trends may only be answered with the heat / cr3 / top1 series;
  2. If an industry has no library yet, say so and give the list of built ones; **never** substitute an industry with a similar name.
"""
from __future__ import annotations

import asyncio

from pydantic_ai import RunContext

from agents.geo.deps import AgentDeps

_TREND_NOTE = (
    "这是**行业**层面的真实跑批数据,不是用户自己品牌的表现 —— 别混说。"
    "问「行业走势」只能用 `行业声量`/`品牌集中度`/`TOP1露出` 里的 series 回答;"
    "返回里没有的走势就说没有,**绝不**拿某一个品牌的数字当行业数字。"
    "百分比已是最终值,不要再乘 100、不要自己换算。"
)


async def get_industry_board(ctx: RunContext[AgentDeps], industry: str = "") -> dict:
    """【读】 Industry leaderboard: who's TOP1 in AI search for an industry, how concentrated it is, the trend. Free and unlimited.

    Without industry, first list the industries **with built libraries**; with it, show that industry's details.

    Use this tool when the user asks "who's number one in our industry / industry landscape / industry trends / how do top brands rank".
    Industry libraries are **built one by one**, not covering all industries: if the user's industry isn't listed, say truthfully it isn't built,
    give them the list, and **never** substitute another similarly named industry's numbers ("industrial cleaning robots" isn't
    "commercial cleaning robots"). For their own brand's performance, that's run_full_diagnosis / the report center.
    """
    # Called directly rather than via scope.call_route: call_route squashes a 404 into {ok, code, message},
    # while the **body of this 404 is the point** — it carries the list of built libraries; without it the model can only say
    # "not found" and we're back on the fabrication path. Both endpoints are anonymous read-only without BackgroundTasks,
    # so call_route's other two duties don't apply here either.
    from fastapi import HTTPException

    from geo.api import industry_library as il

    key = (industry or "").strip()

    def _go() -> dict:
        db = ctx.deps.db
        if not key:
            out = il.list_industries(db=db)
            data = out.model_dump() if hasattr(out, "model_dump") else dict(out)
            data["_note"] = (
                "这是已建库的行业清单。挑一个再调一次本工具看详情。"
                "用户的行业不在里面时如实说「这个行业还没建库」,"
                "**不要**拿别的行业的数字替代;想测他自己的品牌走全面诊断。"
            )
            return data
        try:
            out = il.industry_data(key=key, db=db)
        except HTTPException as e:
            if e.status_code != 404:
                raise
            detail = e.detail if isinstance(e.detail, dict) else {"message": str(e.detail)}
            return {
                "已建库": False,
                "查询的行业": key,
                **detail,
                "_note": "如实告诉用户这个行业还没建库,并把 available 里的清单给他。"
                         "**绝不**用名字相近的行业的数据回答他的问题。",
            }
        data = out.model_dump() if hasattr(out, "model_dump") else dict(out)
        data["已建库"] = True
        data["_note"] = _TREND_NOTE
        return data

    return await asyncio.to_thread(_go)
