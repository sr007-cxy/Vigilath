"""Placement-related conversation tools — select media, add to pending placement, paid orders, backfill URLs.

This group is the last segment of "users can complete the whole flow on their own in conversation". Risk comes in three tiers:

  · Read-only (search_media / list_my_publish_tasks) — given freely
  · Media writes (auto_select_media / push_media_draft / submit_publish_url) — pass the channel gate,
    but **cost nothing**: they stop at creating planned tasks
  · Money-spending (preview_paid_publish / pay_and_publish) — channel gate + spend.py's four caps

**Why `pay_and_publish` only accepts task_ids**: it's a structural design against prompt injection, not an arbitrary
parameter shape. Tasks must first be created by `push_media_draft` / `auto_select_media` under this account's topic;
injected text (body text pasted by the user, others' messages in an IM group, web text in search results) can't invent a valid
task_id; nor is the amount given by the model; it's always determined by live server-side pricing right before the order.

Implementations all use `call_route` to call existing endpoints in-process, **never copying endpoint logic in** —
the endpoints' built-in ownership checks, publish_gate, charge/refund and compliance_audit come for free,
and a copy would inevitably diverge (`tools._review_doc` vs `admin_content_review.approve_doc` is the precedent).
"""
from __future__ import annotations

import asyncio
from typing import Optional

from pydantic_ai import ModelRetry, RunContext

from agents.geo import gates, spend
from agents.geo.deps import AgentDeps


# ── Ownership set of four (self-line version; the admin-line one is in scope.py) ─────────────
def _self_actor(ctx: RunContext[AgentDeps]):
    """The current_user to pass to route functions — **must be a real UserORM**.

    Endpoints read `.id` / `.email` / `.name` (audit trail, notifications); a fake object
    `type("Actor", (), {"id": ...})()` yields a silent empty string or AttributeError when touched.
    Re-query the DB rather than trust the deps snapshot: a ban mid-session takes effect immediately, at the cost of one primary-key lookup.
    """
    from geo.models.user import UserORM

    u = ctx.deps.db.get(UserORM, ctx.deps.user_id)
    if u is None:
        raise ModelRetry("账号不存在或已失效,无法执行该操作。")
    return u


def _own_doc(ctx: RunContext[AgentDeps], doc_id: int):
    from geo.models.ai_telemetry import AiTelemetryTopicORM, TopicGeneratedDocORM

    try:
        did = int(doc_id)
    except (TypeError, ValueError):
        raise ModelRetry("doc_id 必须是整数,先用 list_articles 查真实 id,别臆造。")
    d = ctx.deps.db.get(TopicGeneratedDocORM, did)
    if d is None:
        raise ModelRetry(f"稿件 {did} 不存在。先用 list_articles 查你的稿件 id。")
    t = ctx.deps.db.get(AiTelemetryTopicORM, d.topic_id)
    if t is None or t.user_id != ctx.deps.account_id:
        raise ModelRetry(f"稿件 {did} 不属于当前账号。")
    return d


def _own_task(ctx: RunContext[AgentDeps], task_id: int):
    from geo.models.ai_telemetry import AiTelemetryTopicORM, TopicDocPublishTaskORM

    try:
        tid = int(task_id)
    except (TypeError, ValueError):
        raise ModelRetry("task_id 必须是整数,先用 list_my_publish_tasks 查真实 id。")
    task = ctx.deps.db.get(TopicDocPublishTaskORM, tid)
    if task is None:
        raise ModelRetry(f"投放任务 {tid} 不存在。")
    t = ctx.deps.db.get(AiTelemetryTopicORM, task.topic_id)
    if t is None or t.user_id != ctx.deps.account_id:
        raise ModelRetry(f"投放任务 {tid} 不属于当前账号。")
    return task


def _spend_topic(ctx: RunContext[AgentDeps]):
    """Topic resolution for money-spending tools — **no "most recent batch run" soft fallback allowed**.

    On read-only tools the soft fallback is a convenience (the user is most likely looking at that topic); on money-spending tools it's
    the direct source of "drafts published for the wrong topic". Without an explicit topic let the model ask, following scope.admin_topic's semantics.
    """
    from geo.models.ai_telemetry import AiTelemetryTopicORM

    if not ctx.deps.topic_id:
        raise ModelRetry(
            "这是要花钱的动作,必须先确认是哪个主题/品牌。"
            "用 list_topics 列出来问用户选一个,不要自己挑。"
        )
    t = ctx.deps.db.get(AiTelemetryTopicORM, ctx.deps.topic_id)
    if t is None or t.user_id != ctx.deps.account_id:
        raise ModelRetry("指定主题不存在或不属于当前账号。")
    return t


# ── Read-only ───────────────────────────────────────────────────────────
async def search_media(ctx: RunContext[AgentDeps], keyword: str = "",
                       res_type: str = "Website", max_price_yuan: float = 0,
                       limit: int = 10) -> dict:
    """【读】 Find placeable media in the **media library**, returning media name, price and publish rate.

    Use this tool when the user asks "which media can I place in / any tech ones / which are cheap".
    The price is the **external quote**, i.e. what will actually be charged. Read this number directly to the user.

    ⚠️ The media_id in the results is the credential for placing orders; **only get it from this tool**, don't make one up.
    """
    from agents.geo.scope import call_route
    from geo.api import media_library as ml

    payload = ml.SearchPayload(
        res_type=res_type or "Website",
        keyword=(keyword or "").strip(),
        price_max_cents=int(max_price_yuan * 100) if max_price_yuan else None,
        page=1, page_size=max(1, min(int(limit or 10), 20)),
    )
    out = await call_route(ml.search_media, payload=payload,
                           current_user=_self_actor(ctx), db=ctx.deps.db)
    if isinstance(out, dict) and not out.get("available", True):
        return {"可用": False, "note": out.get("message") or "媒体库暂不可用,请稍后再试"}
    rows = (out or {}).get("rows") or []
    return {
        "可用": True,
        "总数": (out or {}).get("total"),
        "媒体": [{
            "media_id": r["media_id"], "媒体名": r["title"],
            "价格": f"¥{r['price']}", "出稿率": r.get("publish_rate"),
            "频道": r.get("channel") or "", "地区": r.get("region") or "",
        } for r in rows],
        "note": "价格就是实际扣款金额。选好之后用 push_media_draft 把稿子加进待投放。",
    }


async def list_media_candidates(ctx: RunContext[AgentDeps], platform: str = "",
                                limit: int = 10) -> dict:
    """【读】 Which media can be placed on a given **platform**, and roughly how many credits.

    Division of labor with search_media: that one searches by media name, this one looks by platform
    ("can I place on Zhihu", "how many Sohu accounts are there and at what price").
    Use Chinese platform names, e.g. 「知乎」 (Zhihu), 「搜狐号」 (Sohu), 「百家号」 (Baijiahao), 「今日头条」 (Toutiao).
    """
    from geo.services import media_catalog

    plat = (platform or "").strip()
    if not plat:
        raise ModelRetry("要先说是哪个平台(知乎 / 搜狐号 / 百家号 …)。")
    try:
        n = max(1, min(int(limit or 10), 30))
    except (TypeError, ValueError):
        n = 10

    def _go() -> dict:
        # Goes through the media library (mirror table, already deduplicated and merged across suppliers) — the output has no supplier vendor
        # and no purchase price; same metric definition as media selection on the web
        out = media_catalog.search(ctx.deps.db, platform=plat, page=1, page_size=n,
                                   sort="rate_desc", staff=False)
        rows = out.get("rows") or []
        return {
            "平台": plat,
            "可投媒体数": out.get("total") or 0,
            "候选": [{"媒体": r["title"], "价格": f"{r['price_cents']} 积分",
                      "出稿率": r.get("publish_rate"),
                      "价格可能已变动": bool(r.get("price_stale"))} for r in rows],
            "note": "价格是参考价,真正下单前会实时核价。"
                    "要加进待投放用 push_media_draft,或让系统自动挑用 auto_select_media。"
            if rows else "这个平台目前没有可投的媒体,换个平台或用 search_media 按名字找。",
        }

    return await asyncio.to_thread(_go)


async def list_my_publish_tasks(ctx: RunContext[AgentDeps], doc_id: int = 0,
                                status: str = "") -> dict:
    """【读】 My placement tasks: which are pending publish, which have gone out, which need a link backfilled."""
    from agents.geo.tools import _account_topic
    from geo.services import publish_task_service

    # ★ Read-only tools use _account_topic (allowing the "most recent batch run" soft fallback), **not** _spend_topic.
    #   On money-spending tools the soft fallback means "drafts published for the wrong topic"; on read-only tools it's just a convenience —
    #   guarding this like a money-spending tool would bounce even a casual "what have I placed" back with a topic question.
    topic = _account_topic(ctx)
    rows = publish_task_service.list_tasks(
        ctx.deps.db, topic.id,
        doc_id=int(doc_id) or None,
        statuses=[status] if status else None,
    )
    out = [publish_task_service.task_to_dict(r, redact=True, db=ctx.deps.db) for r in rows]
    return {
        "条数": len(out),
        "任务": [{
            "task_id": r["id"], "稿件id": r["doc_id"],
            "媒体": r.get("media_name") or r.get("platform_display") or "",
            "状态": r["status"],
            "价格": f"¥{r['retail_price_yuan']}" if r.get("retail_price_yuan") else "",
            "已扣": f"{int(r.get('charged_cents') or 0)} 积分",
            "链接": r.get("published_url") or "",
        } for r in out],
    }


# ── Media writes (no charge) ────────────────────────────────────────────
async def auto_select_media(ctx: RunContext[AgentDeps], doc_id: int) -> dict:
    """【动作】 Let the system **auto-select media** by historical performance + industry match, and add the draft to pending placement.

    **No charge** — it only goes as far as "added to pending placement"; the real paid order requires calling pay_and_publish separately.
    The draft must already be approved. Results come out in about 10-30 seconds; check with list_my_publish_tasks.
    """
    hit = gates.require(ctx.deps, "publish_own")
    if hit is not None:
        return hit
    from agents.geo.scope import call_route
    from geo.api import rwj_channel as rc

    doc = _own_doc(ctx, doc_id)
    out = await call_route(rc.auto_select_media, doc_id=doc.id,
                           current_user=_self_actor(ctx), db=ctx.deps.db)
    return {"已发起": True, "稿件id": doc.id, **(out or {})}


async def push_media_draft(ctx: RunContext[AgentDeps], doc_id: int,
                           media_ids: list[int]) -> dict:
    """【动作】 Add the draft to the **pending placement** queue of these media.

    **No charge**. media_ids may only come from search_media's return; don't make them up.
    After adding, only pay_and_publish really sends it out and really charges.
    """
    hit = gates.require(ctx.deps, "publish_own")
    if hit is not None:
        return hit
    from agents.geo.scope import call_route
    from geo.api import media_library as ml

    doc = _own_doc(ctx, doc_id)
    ids = [int(i) for i in (media_ids or []) if str(i).strip().isdigit()]
    if not ids:
        raise ModelRetry("要先给出 media_ids(从 search_media 的结果里取),不能为空。")
    out = await call_route(ml.push_draft, doc_id=doc.id,
                           payload=ml.PushDraftPayload(media_ids=ids),
                           current_user=_self_actor(ctx), db=ctx.deps.db)
    tasks = (out or {}).get("tasks") or []
    return {
        "已加入": len(tasks),
        "未加入": (out or {}).get("failed") or [],
        "任务": [{"task_id": t["id"], "媒体": t.get("media_name"),
                  "价格": f"¥{t['retail_price_yuan']}" if t.get("retail_price_yuan") else ""}
                 for t in tasks],
        "note": "还没扣钱。要真的发出去,用 pay_and_publish 带上这些 task_id。",
    }


async def submit_publish_url(ctx: RunContext[AgentDeps], task_id: int, url: str) -> dict:
    """【动作】 Backfill the real link of a published article.

    For drafts published elsewhere by the user, the placement only counts once the link is filled back — only then are retests registered and citation attribution possible.

    ★ Backfilling also **registers a citation retest** (runs after 7 days by default). Retests charge a fixed service fee per run,
      but **only on the day the probe actually runs**, not at registration. The "retest" section in the return body
      must be read truthfully to the user — it's money they didn't actively click for, so it must be stated up front.
    """
    hit = gates.require(ctx.deps, "publish_own")
    if hit is not None:
        return hit
    from agents.geo.scope import call_route
    from geo.api import content as content_api
    from geo.services import retest_service

    task = _own_task(ctx, task_id)
    if not (url or "").strip().startswith("http"):
        raise ModelRetry("url 必须是完整链接(http/https 开头)。")
    out = await call_route(
        content_api.submit_publish_url, task_id=task.id,
        payload=content_api.SubmitUrlPayload(url=url.strip()),
        current_user=_self_actor(ctx), db=ctx.deps.db,
    )
    fee = retest_service.probe_fee_cents(ctx.deps.db)
    return {
        "已回填": True, "task_id": task.id, "状态": (out or {}).get("status"),
        "复测": {
            "会不会跑": "会,发布后约 "
                        f"{retest_service.RETEST_DELAY_DAYS} 天自动复测一次这批提示词",
            "费用": f"{fee} 积分" if fee > 0 else "0 积分(当前不收费)",
            "什么时候扣": "复测**真的跑起来那天**才扣;主题的持续监测是关着的话,"
                          "复测会被取消,那就一分不扣",
        },
        "_note": "把「复测」这一段主动说给用户 —— 他没点过这个动作,"
                 "不说清楚就是在他不知情的时候花他的钱。",
    }


# ── Money-spending ──────────────────────────────────────────────────────
async def preview_paid_publish(ctx: RunContext[AgentDeps], task_ids: list[int]) -> dict:
    """【读】 Pre-placement pricing: how much each of these costs now, the total, and whether the balance suffices.

    **Quote with it before spending money**, stating "what's done / how much / remaining balance" all at once.
    """
    # Even read-only must pass the run-time gate: load time only guarantees "external channels can't get the schema",
    # while this return body contains real quotes and balances, which shouldn't be exposed on channels without the paid capability
    hit = gates.require(ctx.deps, "publish_paid")
    if hit is not None:
        return hit
    from agents.geo.scope import call_route
    from geo.api import rwj_channel as rc
    from geo.services import balance_service

    ids = [_own_task(ctx, t).id for t in (task_ids or [])]
    if not ids:
        raise ModelRetry("要先给出 task_ids(从 list_my_publish_tasks 或 push_media_draft 拿)。")
    out = await call_route(rc.batch_publish_preview,
                           payload=rc.BatchPreviewPayload(task_ids=ids),
                           current_user=_self_actor(ctx), db=ctx.deps.db)
    total = (out or {}).get("total_retail_cents") or 0
    bal = balance_service.get_balance(ctx.deps.db, ctx.deps.user_id)
    return {
        "家数": (out or {}).get("count"),
        "明细": [{"task_id": i["task_id"], "媒体": i.get("media_name"),
                  "价格": f"¥{i.get('retail_price')}"}
                 for i in ((out or {}).get("items") or [])],
        "合计": f"{int(total)} 积分",
        "合计_分": total,
        "当前积分": f"{int(bal)}",
        "积分是否够": bal >= total,
        "拿不到报价的": (out or {}).get("failed") or [],
    }


async def pay_and_publish(ctx: RunContext[AgentDeps], task_ids: list[int],
                          confirm_token: str = "") -> dict:
    """【动作·花钱】 **Real paid placement** — deduct from the account balance and publish the draft to these media.

    ⚠️ Irreversible. task_ids may only come from list_my_publish_tasks / push_media_draft; don't make them up.

    **Two-step confirmation**: the first call without confirm_token returns a preview based on **live pricing**
    (which media, each price, the total, remaining balance). Read it item by item to the user and **end this turn**;
    only after their next message explicitly confirms, call again with the same params + confirm_token to actually order.

    With insufficient balance / over a cap, no order is placed; the return body says how much is missing and where to top up — just relay it truthfully.
    """
    from agents.geo import confirm
    from agents.geo.scope import call_route
    from geo.api import rwj_channel as rc

    tasks = [_own_task(ctx, t) for t in (task_ids or [])]
    if not tasks:
        raise ModelRetry("要先给出 task_ids,不能为空。")
    _spend_topic(ctx)                      # Money-spending actions don't accept the "most recent batch run" soft fallback

    # Price first to get each media's live external price — the amount **is not given by the model**
    quote = await call_route(rc.batch_publish_preview,
                             payload=rc.BatchPreviewPayload(task_ids=[t.id for t in tasks]),
                             current_user=_self_actor(ctx), db=ctx.deps.db)
    items = (quote or {}).get("items") or []
    if not items:
        return {"已下单": 0, "note": "这几条任务都拿不到报价,没有下单。",
                "失败": (quote or {}).get("failed") or []}
    total = int((quote or {}).get("total_retail_cents") or 0)

    plan = spend.SpendPlan(
        tool="pay_and_publish", amount_cents=total,
        ref_id=",".join(str(i["task_id"]) for i in sorted(items, key=lambda x: x["task_id"])),
    )
    cooled = spend.cooled_down(plan)
    if cooled is not None:
        return cooled
    blocked = spend.precheck(ctx.deps, plan)
    if blocked is not None:
        return blocked

    # Two-step confirmation. **Placed after pricing**: the money in the preview must be the live price just computed by the server,
    # not an old price paraphrased by the model. The args fingerprint uses the sorted, deduplicated id list, so a different order doesn't count as changed args.
    confirm_args = {"task_ids": sorted({int(i["task_id"]) for i in items})}
    from geo.services import balance_service

    def _preview() -> dict:
        bal = balance_service.get_balance(ctx.deps.db, ctx.deps.user_id)
        return confirm.needs_confirm(
            ctx.deps, tool="pay_and_publish",
            action_label=f"付费投放 {len(items)} 家媒体",
            args=confirm_args,
            preview={"明细": [{"task_id": i["task_id"], "媒体": i.get("media_name"),
                               "价格": f"¥{i.get('retail_price')}"} for i in items],
                     "家数": len(items)},
            impact=["会**真实扣账户积分**并把文章发到外部媒体"],
            irreversible=["发出去撤不回来,钱也退不回来"],
            cost={"合计": f"{total} 积分", "当前积分": f"{int(bal)}",
                  "扣后积分": f"{int(bal - total)}" if bal >= total else None,
                  "积分是否够": bal >= total},
            note="价格是刚刚实时核的。用户确认前价格变了会重新核价,**绝不**拿旧价下单。",
        )

    if not confirm_token:
        return _preview()
    stop = confirm.guard(ctx.deps, confirm_token, tool="pay_and_publish",
                         args=confirm_args, rebuild_preview=_preview)
    if stop is not None:
        return stop

    if spend.dry_run():
        return {"dry_run": True, "已下单": 0, "合计": f"{int(total)} 积分",
                "note": "干跑模式:全链路走通但没有真的下单、没有扣钱。"}

    # marked() makes this ledger entry carry via=agent — the daily-amount / daily-count gates only recognize this tag;
    # not wrapping it means those two gates don't exist
    with spend.marked():
        out = await call_route(
            rc.batch_publish,
            payload=rc.BatchPublishPayload(items=[
                rc.BatchPublishItem(task_id=i["task_id"],
                                    expected_price_cents=i.get("retail_price_cents"))
                for i in items
            ]),
            current_user=_self_actor(ctx), db=ctx.deps.db,
        )
    succeeded = (out or {}).get("succeeded") or []
    result = {
        "已下单": len(succeeded),
        "实扣": f"{int(((out or {}).get('total_charged_cents') or 0))} 积分",
        "失败": (out or {}).get("failed") or [],
        "未执行": (out or {}).get("skipped") or [],
        "中止": bool((out or {}).get("aborted")),
        "note": "已经真实发出去了,不可撤销。出稿后用 list_my_publish_tasks 看链接。",
    }
    return spend.remember(plan, result)
