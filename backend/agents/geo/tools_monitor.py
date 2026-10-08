"""Continuous monitoring cadence and manual batch runs.

These two tools were added for the same reason: **after persisting to DB, nothing happened**.

Topics default to weekly, the cycle anchor defaults to Monday 02:05 Beijing time, and the batch run center ticks hourly. A user submits in the morning,
nothing happens that day, and the next day an early-morning task shows up. Meanwhile on the agent side:
  · "batch run" is a routing keyword (_GEO_KW in agent.py), so such questions are sent to the GEO line;
  · but the GEO line **had no tool to start a batch run**; `get_run_status` can only read;
  · while `get_price_quote(kind="batch")` can quote a batch run — it can quote but not order;
  · and the prompt forbids the model from saying "I can't reach that tool".
With all four together, the model could only invent a platform step for the user to click (the "the web app has a batch run button" line in testing
came from this, and the web app had no such button).

So: if it can be started, really start it (`trigger_batch_run`); if the cadence is unclear, make it clear (`get_monitor_schedule`).
Starting is a **money-spending** action, going through confirm.py's two-step confirmation + spend.py's caps; both layers must pass.
"""
from __future__ import annotations

import asyncio

from pydantic_ai import ModelRetry, RunContext

from agents.geo import gates
from agents.geo.deps import AgentDeps


def _topic_or_retry(ctx: RunContext[AgentDeps], topic_id: int | None):
    from agents.geo.tools import _account_topic
    from geo.models.ai_telemetry import AiTelemetryTopicORM

    if topic_id:
        t = ctx.deps.db.get(AiTelemetryTopicORM, int(topic_id))
        if t is None or t.user_id != ctx.deps.account_id:
            raise ModelRetry("指定主题不存在或不属于当前账号。先用 list_topics 查清 id。")
        return t
    t = _account_topic(ctx)
    if t is None:
        raise ModelRetry("当前账号还没有主题,没有可跑的提示词。"
                         "先问用户要不要建一个主题(create_topic),不要自己建。")
    return t


async def get_monitor_schedule(ctx: RunContext[AgentDeps],
                               topic_id: int | None = None) -> dict:
    """【读】 Continuous monitoring cadence: how often it runs, when it starts, how many Q&As and credits per round, how to stop.

    Use this tool when the user asks "how often does it run / when are results out / will it keep running / how to stop / how much per round".

    ★ After persisting (set_selected_queries) it **doesn't start immediately**; it waits for the next cycle anchor.
      This must be told to the user proactively — otherwise they'll stare at the page waiting for a result that won't come that day.
    """
    from geo.services.topic_schedule import describe

    topic = _topic_or_retry(ctx, topic_id)

    def _go() -> dict:
        return {"topic_id": topic.id, "主题": topic.name, **describe(topic, ctx.deps.db)}

    return await asyncio.to_thread(_go)


async def set_monitor_schedule(ctx: RunContext[AgentDeps],
                               cadence: str = "",
                               pause_days: int | None = None,
                               resume: bool = False,
                               topic_id: int | None = None) -> dict:
    """【写】 Turn on / off / adjust **the continuous monitoring cadence**. Reversible and free itself; change it directly when the user says so.

    Use this tool when the user says "enable monitoring / turn on scheduled audits / run daily instead / pause for now / stop running /
    pause for two weeks / resume monitoring".

    ★ **It can be done right in the conversation**. Previously only the ops console could change it, so all the copy said "go to the topic settings page
      and turn off scheduled audits" — while the front end had no such switch at all. Don't answer users that way again.

    cadence: `daily` one round per day / `weekly` one round per week / `off` stop running; omitted = cadence unchanged.
    pause_days: temporarily pause N days (1–90), auto-resumes when due without losing config; can be used separately from cadence.
    resume: True = end the pause immediately.

    After the change it **doesn't start immediately**; it waits for the next cycle anchor — tell the user this proactively.
    To run a round now, use trigger_batch_run (that one spends credits).
    """
    from agents.geo.scope import call_route
    from agents.geo.tools_publish import _self_actor
    from geo.api import ai_telemetry as at

    topic = _topic_or_retry(ctx, topic_id)
    tier = (cadence or "").strip().lower()
    if tier and tier not in ("daily", "weekly", "off"):
        raise ModelRetry("cadence 只能是 daily / weekly / off。")
    if not tier and pause_days is None and not resume:
        raise ModelRetry("没说要改成什么。先问清用户是要「每天跑 / 每周跑 / 停掉 / 暂停几天 / 恢复」。")
    if pause_days is not None and not 1 <= int(pause_days) <= 90:
        raise ModelRetry("pause_days 只能是 1~90 天。")

    payload = at.SelfSchedulePatch(
        schedule_tier=tier or None,
        pause_days=int(pause_days) if pause_days is not None else None,
        clear_pause=bool(resume),
    )
    out = await call_route(at.patch_own_topic_schedule, topic_id=topic.id,
                           payload=payload, current_user=_self_actor(ctx),
                           db=ctx.deps.db)
    return {"已修改": True, **(out if isinstance(out, dict) else {}),
            "_note": "节奏已改。提醒用户:**这一刻不会开跑**,要等下一个周期锚点;"
                     "想现在就跑一轮用 trigger_batch_run(那个按问答数扣积分)。"}


async def trigger_batch_run(ctx: RunContext[AgentDeps], topic_id: int | None = None,
                            confirm_token: str = "") -> dict:
    """【动作·花钱】 Manually run one monitoring batch round right now (without waiting for the next cycle anchor).

    Use this tool when the user says "run a round now / batch run immediately / don't wait for next week, run once first".
    Merely asking "how far along is it / how did the last run go" is a **view** intent -> get_run_status.

    **Two-step confirmation**: first call without confirm_token to get a preview, read "how many Q&As, how many credits,
    remaining balance" truthfully to the user and **end this turn**; after their next message explicitly confirms, call again with the same params +
    confirm_token to actually start.

    It's async after starting; a round takes minutes to tens of minutes; check progress with get_run_status, don't poll in place.
    Engine selection and regular batch frequency are still fixed by the platform; a manual round doesn't change scheduling.
    """
    from agents.geo import confirm, spend
    from agents.geo.tools_billing import quote
    from geo.services.topic_schedule import selected_query_count

    hit = gates.require(ctx.deps, "wallet_spend")
    if hit is not None:
        return hit
    # Money-spending actions may not guess the topic: in multi-topic accounts a wrong guess burns a round of engine capacity on another brand's terms.
    from agents.geo.tools import _explicit_topic

    topic, ask = _explicit_topic(ctx, topic_id, "trigger_batch_run")
    if ask is not None:
        return ask

    import json as _json

    try:
        engines = len(_json.loads(topic.engines_json or "[]"))
    except Exception:  # noqa: BLE001
        engines = 0
    queries = selected_query_count(topic)
    cells = queries * engines
    if cells <= 0:
        # State clearly **which piece is missing** and give a real next step. Previously it only said "persist to DB first",
        # and the model added "go to the web app and turn on the monitoring schedule switch" on its own — that switch didn't exist on the front end then.
        lack = []
        if queries <= 0:
            lack.append("还没有勾选中的提示词(用 set_selected_queries 落库,"
                        "或到主题设置页的「提示词」标签页勾选)")
        if engines <= 0:
            lack.append("这个主题还没有配监测引擎(引擎由平台配置,告诉用户我们来配)")
        return {
            "ok": False, "modified": False, "topic_id": topic.id, "主题": topic.name,
            "提示词数": queries, "引擎数": engines,
            "缺什么": lack,
            "note": ("这个主题现在没有可跑的内容,所以**没有发起**任何跑批。"
                     "按「缺什么」如实告诉用户差哪一样。"
                     "**不要**编造「先去网页端打开某个开关」这类步骤 —— "
                     "持续监测的节奏开关用 set_monitor_schedule 就能改,"
                     "而它不是这里跑不了的原因。"),
        }

    args = {"topic_id": topic.id}
    q = quote(ctx.deps.db, ctx.deps.user_id, "batch", cells)
    amount = int(q.get("_total_cents") or 0)

    def _preview() -> dict:
        return confirm.needs_confirm(
            ctx.deps, tool="trigger_batch_run", action_label="立刻手动跑一轮监测跑批",
            args=args, topic=topic,
            preview={"主题": topic.name, "提示词数": queries, "引擎数": engines,
                     "本轮问答数": cells},
            impact=["立刻发起一轮跑批,不等下一个周期锚点",
                    "一轮几分钟到几十分钟,跑完用 get_run_status 看结果",
                    "不改变常规调度,下一个周期照常还会跑"],
            irreversible=["发出去的引擎调用撤不回来"],
            cost={k: v for k, v in q.items() if not k.startswith("_")},
            # Whether it's really charged is decided by quote() (batch runs are still in the preview period by default, nothing charged) —
            # no longer unconditionally write 「次日结算」 ("settled next day"); read the note from cost instead.
            note="一次问答 = 一个提示词 × 一个引擎问一遍。计费口径见上面的报价,按实际跑成的问答数计。",
        )

    if not confirm_token:
        return _preview()
    blocked = confirm.guard(ctx.deps, confirm_token, tool="trigger_batch_run",
                            args=args, rebuild_preview=_preview)
    if blocked is not None:
        return blocked

    # Confirmed; now pass the spending caps once more: two-step confirmation guards against "the model acting on its own",
    # spend's caps guard against "a large sum confirmed away repeatedly within a day". Neither replaces the other.
    #
    # ⚠️ Note that **spend.marked() doesn't apply here**: batch runs are settled the next day by the number of Q&As actually completed,
    #    and the charge ledger entry is written by the settlement pipeline, not in this process, so it can't be stamped via=agent. Hence spend's
    #    ④ daily amount / ⑤ daily count gates can't count manual batch runs — what really stops repeated clicks is
    #    `cooled_down` (returns the last result within the same topic's cooldown) and the two-step confirmation itself.
    #    Pre-charging here is a different matter; don't do it in passing: it would double-count with next-day settlement.
    plan = spend.SpendPlan(tool="trigger_batch_run", amount_cents=amount,
                           ref_id=f"topic:{topic.id}", cap="wallet_spend")
    stop = spend.precheck(ctx.deps, plan)
    if stop is not None:
        return stop
    cooled = spend.cooled_down(plan)
    if cooled is not None:
        return cooled

    from agents.geo.scope import call_route
    from agents.geo.tools_publish import _self_actor
    from geo.api import ai_telemetry as at

    out = await call_route(at.trigger_run, topic_id=topic.id,
                           current_user=_self_actor(ctx), db=ctx.deps.db)
    if isinstance(out, dict) and out.get("ok") is False:
        return out                      # 502 batch run center unreachable and the like, passed through as-is for the model to relay
    return spend.remember(plan, {
        "ok": True, "已发起": True, "topic_id": topic.id, "主题": topic.name,
        "本轮问答数": cells, "预计扣费": q.get("合计"),
        "本次是否真扣": q.get("本次是否真扣", True),
        "计费说明": q.get("note") or "按实际跑成的问答数计,次日结算。",
        "note": "已经发起一轮跑批,异步跑,几分钟到几十分钟。"
                "用 get_run_status 看进度,**不要在本轮反复查**。"
                "计费照「计费说明」如实转述 —— 跑批在预览期时是**真的不扣**,"
                "别说成「次日会扣」让用户等一笔不会发生的账。",
    })
