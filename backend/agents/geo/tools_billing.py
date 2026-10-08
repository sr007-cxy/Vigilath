"""Credit-related conversation tools — check credits, quotes, ledger, top-up (including creating top-up orders), sentiment plans.

It's a separate file because `tools.py` is already 1800 lines; piling more in would make it unreadable.

Mostly read-only; the two exceptions are `start_sentiment_monitor` (enable daily sentiment monitoring) and
`create_topup_order` (create a WeChat top-up order), both through `confirm.py`'s two-step confirmation.
The actions that really charge money for media publishing are in `tools_publish.py`, and they also pass `spend.py`'s caps.
Why "check" tools are needed: on hitting insufficient credits the model could only say "insufficient credits" in the return body, and the user
didn't know how much they had, how much this run needed, or where to top up — that's not self-service.

★ **There's only one copy of the top-up directions** (`TOPUP_HINT` / `TOPUP_PATH`). The same thing used to be described three ways
  — 「账户 → 我的积分」 ("Account -> My Credits"), 「/account/balance」, 「账户 → 账户余额」 ("Account -> Account Balance") — and users following the last one
  couldn't find it. Anywhere that needs to say "where to top up" takes it from here.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from pydantic_ai import ModelRetry, RunContext

from agents.geo import gates
from agents.geo.deps import AgentDeps

TOPUP_PATH = "/account/balance"
TOPUP_HINT = f"到「账户 → 我的积分」({TOPUP_PATH})充值"


def _points(cents: Optional[int]) -> str:
    """Credit display. **1 CNY = 100 credits = the "cents" in the DB**, so 1:1 with no division."""
    return f"{int(cents or 0)} 积分"


# Billing item -> price key. Quotes and confirmation cards share the same table; no separate copies.
PRICE_KINDS = ("article", "batch", "sentiment", "sentiment_daily")


def quote(db, user_id: int, kind: str, count: int = 1) -> dict:
    """Compute how much one run costs — **the single algorithm for quotes and confirmation cards**.

    Extracted because the two-step confirmation's `cost` and `get_price_quote` must give the same number:
    computing separately will drift sooner or later, and "preview says 30, actual charge 50" is worse than no confirmation.

    ★ When the price can't be fetched, **return an explanation instead of raising**. It's called by the two-step confirmation preview, which is
      a link in the "stop and ask the user" chain — letting a quote failure blow up the whole confirmation flow results in
      either the action being impossible or (worse) someone removing the confirmation to bypass it. If the price is unavailable, state it on the card.
    """
    from geo.models.balance import (
        PRICE_BATCH_CELL, PRICE_CONTENT_DOC, PRICE_SENTIMENT_DAILY, PRICE_SENTIMENT_ONCE,
    )
    from geo.services import balance_service

    keys = {"article": PRICE_CONTENT_DOC, "batch": PRICE_BATCH_CELL,
            "sentiment": PRICE_SENTIMENT_ONCE, "sentiment_daily": PRICE_SENTIMENT_DAILY}
    key = keys.get((kind or "").strip().lower())
    if key is None:
        return {"error": f"不认识的计费项 {kind!r},可选:" + " / ".join(PRICE_KINDS)}
    n = max(1, int(count or 1))
    try:
        unit = balance_service.get_price(db, key)
        bal = balance_service.get_balance(db, user_id)
    except Exception:  # noqa: BLE001
        return {"计费项": kind, "数量": n, "合计": None, "_total_cents": 0,
                "note": "这一单的报价暂时取不到。**如实告诉用户拿不到准确价格**,"
                        f"让他到网页确认后再决定,不要自己估一个数。{TOPUP_HINT}可看余额。"}
    total = unit * n
    # The quote must say **whether this charge will really be deducted**. A number on the price list doesn't mean it will be charged now:
    #   · GEO_TOPIC_CHARGE_ENABLED=0 -> all topic-level charges are computed but not deducted;
    #   · batch runs have a separate gate (GEO_BATCH_CHARGE_MODE=shadow is the default) -> nothing is charged.
    # Previously the card only read the price list; users reconciled against it, found "said it would charge but didn't", and could only suspect bad bookkeeping.
    charged, why = _will_charge(kind, unit)
    out = {
        "计费项": kind, "数量": n,
        "单价": _points(unit), "合计": _points(total) if charged else "0 积分",
        "当前积分": _points(bal),
        "扣后积分": _points(bal - total) if (charged and bal >= total) else _points(bal),
        "积分是否够": (bal >= total) if charged else True,
        "_total_cents": int(total) if charged else 0,
        "note": ("" if charged else why) or
                ("" if bal >= total else f"积分不够,还差 {_points(total - bal)}。{TOPUP_HINT}。"),
    }
    if not charged:
        out["本次是否真扣"] = False
        out["按价目表本应"] = _points(total)
    return out


def _will_charge(kind: str, unit: int) -> tuple[bool, str]:
    """Whether this charge will really be deducted now. Returns (will it, reason if not)."""
    from geo.services import topic_billing

    if unit <= 0:
        return False, "这一项当前单价为 0,照常执行、不扣积分。"
    if not topic_billing.enabled():
        return False, "平台当前对这一项**只计量不扣费**,这次不会扣积分。"
    if (kind or "").strip().lower() == "batch":
        from geo.services import batch_billing

        if batch_billing.mode() != "enforce":
            return False, ("监测跑批目前是**预览期,不扣积分**(只记账)。"
                           "如实这么说,别让用户以为跑一轮就要花钱。")
        if not batch_billing.billing_start_date():
            return False, "监测跑批还没有设定计费起始日,这段时间不扣积分。"
    return True, ""


async def get_wallet(ctx: RunContext[AgentDeps]) -> dict:
    """【读】 Check the current account's **credit balance** and unit prices.

    Use this tool when the user asks "how many credits do I have / balance / is it enough / how much per article".
    Also use it first when placing an order with insufficient credits, to state clearly "how much is missing" rather than just "insufficient credits".
    """
    def _go() -> dict:
        from geo.models.balance import (
            PRICE_BATCH_CELL, PRICE_CONTENT_DOC, PRICE_SENTIMENT_DAILY,
            PRICE_SENTIMENT_ONCE,
        )
        from geo.services import balance_service

        db = ctx.deps.db
        acc = balance_service.get_or_create_account(db, ctx.deps.user_id)
        db.commit()                     # Lazy grants must be persisted, otherwise new users see 1000 credits but there's no DB row
        return {
            "积分余额": _points(acc.balance_cents),
            "积分_分": int(acc.balance_cents or 0),
            "累计充值": _points(acc.recharged_cents),
            "累计消费": _points(acc.spent_cents),
            "单价": {
                "监测每次引擎提问": _points(balance_service.get_price(db, PRICE_BATCH_CELL)),
                "文章生成每篇": _points(balance_service.get_price(db, PRICE_CONTENT_DOC)),
                "舆情快检每单": _points(balance_service.get_price(db, PRICE_SENTIMENT_ONCE)),
                "舆情日常监控每天": _points(
                    balance_service.get_price(db, PRICE_SENTIMENT_DAILY)),
            },
            "note": "付费投放按媒体实时报价即时扣、失败全额退;监测跑批次日结算。"
                    f"要充值就{TOPUP_HINT}。",
            "_note": "这是**余额**。用户问「花在哪了 / 消费明细 / 为什么少了」时,"
                     "**必须再调 list_ledger 拿真实流水**,绝不能拿余额差额去推断明细 —— "
                     "推断出来的明细全是编的。",
        }

    return await asyncio.to_thread(_go)


async def get_price_quote(ctx: RunContext[AgentDeps], kind: str, count: int = 1) -> dict:
    """【读】 Quote: how much this will cost.

    kind is one of: `article` (article generation, per article) / `batch` (monitoring batch run, per "question × engine" cell) /
    `sentiment` (sentiment quick check, per run) / `sentiment_daily` (daily sentiment monitoring, per day). count is the quantity.

    **Quote before starting** any money-spending action — state "what's done / how many credits / how much remains"
    all at once; don't let the user find out about the charge only after clicking.
    """
    def _go() -> dict:
        out = quote(ctx.deps.db, ctx.deps.user_id, kind, count)
        out.pop("_total_cents", None)          # Internal field, not shown to the model
        return out

    return await asyncio.to_thread(_go)


async def list_ledger(ctx: RunContext[AgentDeps], limit: int = 10) -> dict:
    """【读】 Recent account ledger (where it was spent / how much was topped up).

    Use this tool when the user asks "where did the money go / why is it less / how much was charged last time".
    """
    def _go() -> dict:
        import json

        from geo.models.balance import BalanceLedgerORM

        rows = (ctx.deps.db.query(BalanceLedgerORM)
                .filter(BalanceLedgerORM.user_id == ctx.deps.user_id)
                .order_by(BalanceLedgerORM.id.desc())
                .limit(max(1, min(int(limit or 10), 50))).all())
        # **Same definition** as KIND_LABELS in frontend Account/BalanceTab.tsx; changing one requires syncing the other.
        labels = {"grant": "注册赠送", "recharge": "充值", "refund": "退款",
                  "adjust": "人工调账", "batch": "监测跑批",
                  "full_diagnosis": "一键全面诊断", "content_doc": "文章生成",
                  "content_image": "配图", "compliance": "合规审核",
                  "publish_media": "付费投放", "retest": "被引复测",
                  "sentiment_once": "舆情快检", "sentiment_daily": "舆情日常监控"}
        out = []
        for r in rows:
            note = {}
            if r.note_json:
                try:
                    note = json.loads(r.note_json)
                except (TypeError, ValueError):
                    note = {}
            out.append({
                "时间": str(r.created_at)[:19],
                "项目": labels.get(r.kind, r.kind),
                "方向": "入账" if r.direction == "credit" else "支出",
                "金额": _points(r.amount_cents),
                "积分余额": _points(r.balance_after_cents),
                "说明": note.get("media") or note.get("brand") or note.get("reason") or "",
            })
        return {"条数": len(out), "流水": out}

    return await asyncio.to_thread(_go)


async def get_topup_options(ctx: RunContext[AgentDeps]) -> dict:
    """【读】 How to top up. Used to give directions when credits are insufficient.

    ★ This **only reports paths that really work**. It used to hard-code 「对话内直付: 可用」 ("in-chat direct pay: available") (based on env
      AGENT_TOPUP_ENABLED), while no tool actually delivered it — the model read the line truthfully,
      the user said "then top up for me", and the model had no next step. Now the criterion is whether `create_topup_order`
      is actually loaded this turn (channel gate wallet_spend); only then does it say top-up is possible in conversation.
    """
    in_chat = gates.allow(ctx.deps, "wallet_spend")
    out = {
        "网页充值": f"{TOPUP_PATH}(微信扫码,¥10 起充)",
        "对话内充值": ("可用 —— 用 create_topup_order 出一张微信扫码充值单"
                       if in_chat else "本通道不开放,请走网页充值"),
        "note": f"积分不足就{TOPUP_HINT};充值到账后这边继续。"
                "**绝不要**承诺一条本轮工具里没有的充值方式。",
    }
    return out


async def create_topup_order(ctx: RunContext[AgentDeps], amount_yuan: float,
                             confirm_token: str = "") -> dict:
    """【动作】 Create a **WeChat QR top-up order**, returning the QR code content and order number.

    This is the only path in conversation that really tops up (uses the same order endpoint as the web, the callback credits by purpose,
    no plan is activated). Use this tool when the user says "top up 100 for me" or "top up".

    Two steps: first call without confirm_token to get a preview (amount, current credits),
    read the preview to the user and **end this turn**; after their next message explicitly confirms, call again with the returned confirm_token
    and **exactly the same params** to actually place the order.

    **Crediting depends on the WeChat callback** — an order isn't the money arriving. When the user says they've paid, verify the balance with get_wallet;
    don't say "credited" just on their word.
    """
    from agents.geo import confirm

    hit = gates.require(ctx.deps, "wallet_spend")
    if hit is not None:
        return {**hit, "网页充值": TOPUP_PATH,
                "note": f"本通道不能发起充值单。请{TOPUP_HINT}。"}
    try:
        cents = int(round(float(amount_yuan) * 100))
    except (TypeError, ValueError):
        raise ModelRetry("充值金额要是数字(元),比如 100。先问清用户要充多少。") from None
    # The amount range is also validated in the order function, but by then the confirmation card has already been read to the user — block early,
    # so "invalid amount" doesn't pop up only after "Confirm"
    from geo.services.wechat_pay_service import TOPUP_MAX_CENTS, TOPUP_MIN_CENTS

    if cents < TOPUP_MIN_CENTS or cents > TOPUP_MAX_CENTS:
        raise ModelRetry(
            f"单笔充值范围 ¥{TOPUP_MIN_CENTS / 100:.0f}–¥{TOPUP_MAX_CENTS / 100:.0f}。"
            "把这个范围告诉用户,让他重新给一个金额,不要替他决定。")

    args = {"amount_yuan": float(amount_yuan)}

    def _preview() -> dict:
        from geo.services import balance_service

        bal = balance_service.get_balance(ctx.deps.db, ctx.deps.user_id)
        return confirm.needs_confirm(
            ctx.deps, tool="create_topup_order", action_label="发起微信充值单",
            args=args,
            preview={"充值金额": f"¥{cents / 100:.2f}", "到账积分": _points(cents),
                     "当前积分": _points(bal), "充值后": _points(bal + cents)},
            impact=["会生成一张微信扫码支付单;扫码付款后积分自动入账"],
            irreversible=[],
            cost={"金额": f"¥{cents / 100:.2f}", "扣的是": "微信,不是积分余额"},
            note="金额原样念给用户核对,**不要替他决定充多少**。",
        )

    if not confirm_token:
        return _preview()
    blocked = confirm.guard(ctx.deps, confirm_token, tool="create_topup_order",
                            args=args, rebuild_preview=_preview)
    if blocked is not None:
        return blocked

    def _go() -> dict:
        from geo.api.wechat_payment import wechat_pay_service
        from geo.models.user import UserORM

        user = ctx.deps.db.get(UserORM, ctx.deps.user_id)
        if user is None:
            return {"ok": False, "note": "当前账号读不到,充值单没有下。"}
        return wechat_pay_service.create_topup_native_order(user=user, amount_cents=cents)

    from geo.utils.error_handler import AppException

    try:
        order = await asyncio.to_thread(_go)
    except AppException as e:
        # WeChat Pay not configured / amount out of range / WeChat-side error: the backend's message is more accurate than anything made up here
        return {"ok": False, "modified": False,
                "note": f"{getattr(e, 'message', '') or '充值单没下成'}。请{TOPUP_HINT}。"}
    except Exception as e:  # noqa: BLE001 — payment-side exceptions shouldn't surface as an error stack
        return {"ok": False, "modified": False,
                "note": f"充值单没下成({type(e).__name__})。请{TOPUP_HINT}。"}
    if isinstance(order, dict) and order.get("ok") is False:
        return order
    body = order if isinstance(order, dict) else {}
    return {
        "ok": True,
        "充值金额": f"¥{cents / 100:.2f}",
        "订单号": body.get("out_trade_no") or body.get("order_no") or "",
        "二维码内容": body.get("code_url") or "",
        "网页充值": TOPUP_PATH,
        "note": "把二维码内容/链接给用户,让他在微信里扫码支付。"
                "**到账以微信回调为准** —— 他说付好了之后用 get_wallet 复核余额再回话,"
                "别凭一句「付了」就说已到账。同一单不要重复下,重复下会产生第二笔。",
    }


async def get_sentiment_plan(ctx: RunContext[AgentDeps]) -> dict:
    """【读】 How sentiment monitoring is charged and how many brands are currently monitored.

    Use this tool when the user asks "how much is sentiment monitoring / should I buy a plan / which brands am I monitoring".
    Metric definition: **charged per use, no monthly plan**. Quick checks are charged per run, daily monitoring per day (once a day,
    charged only once no matter how many rounds run that day).
    """
    def _go() -> dict:
        from geo.models.balance import PRICE_SENTIMENT_DAILY, PRICE_SENTIMENT_ONCE
        from geo.models.sentiment import SentimentAccountORM
        from geo.services import balance_service

        db = ctx.deps.db
        once_fee = balance_service.get_price(db, PRICE_SENTIMENT_ONCE)
        daily_fee = balance_service.get_price(db, PRICE_SENTIMENT_DAILY)
        rows = (db.query(SentimentAccountORM)
                .filter(SentimentAccountORM.user_id == ctx.deps.user_id,
                        SentimentAccountORM.active.is_(True)).all())
        return {
            "计费方式": "按次收费,没有包月套餐",
            "快检每单": _points(once_fee),
            "日常监控每天": _points(daily_fee),
            "当前监控品牌": [r.target or r.ticker for r in rows],
            "品牌数": len(rows),
            "当前积分": _points(balance_service.get_balance(db, ctx.deps.user_id)),
            "note": "单次快检:报个品牌名就能跑,不用先开通,用 run_sentiment_once。"
                    "日常监控:每天自动跑一轮 + 负面预警 + 历史回溯,一天扣一次,"
                    "用 start_sentiment_monitor 就能开通。积分不足只会跳过当轮,不会停用。",
        }

    return await asyncio.to_thread(_go)


async def start_sentiment_monitor(
    ctx: RunContext[AgentDeps],
    brand: str = "",
    keywords: list[str] | None = None,
    aliases: list[str] | None = None,
    run_now: bool = True,
    confirm_token: str = "",
) -> dict:
    """【动作·花钱】 Enable **daily sentiment monitoring** — runs automatically once a day, with negative alerts and history lookback.

    Difference from the one-off quick check: a quick check is one-time and gone once viewed; daily monitoring is continuous, charging credits per day
    (once a day, charged only once no matter how many rounds run), and insufficient credits only skip that round without disabling it.

    Use this tool when the user says "keep watching for me / check daily / enable monitoring / notify me of negatives".
    If they only say "check for negatives", use run_sentiment_once; don't push them straight into enabling.

    **Two-step confirmation**: first call without confirm_token to get a preview, read "how much per day / current credits"
    truthfully to the user and **end this turn**; after their next message explicitly confirms, call again with the same params + confirm_token.

    brand: brand name (defaults to the current topic name); keywords: words to watch together; aliases: brand aliases.
    """
    from agents.geo import confirm

    # Enabling itself costs nothing, but it makes the account be charged once daily from tomorrow — so it's in the wallet_spend tier;
    # neither external channels nor the ops console should flip this switch for customers
    hit = gates.require(ctx.deps, "wallet_spend")
    if hit is not None:
        return hit

    from agents.geo.scope import call_route
    from agents.geo.tools_publish import _self_actor
    from geo.api import sentiment as sa
    from geo.models.sentiment import SentimentAccountORM

    name = (brand or "").strip()
    if not name:
        from agents.geo.tools import _account_topic
        t = _account_topic(ctx)
        name = (getattr(t, "name", "") or "").strip() if t else ""
    if not name:
        raise ModelRetry("要开通日常监控得先知道盯哪个品牌,问用户要一个品牌名。")

    db = ctx.deps.db
    dup = (db.query(SentimentAccountORM)
             .filter(SentimentAccountORM.user_id == ctx.deps.user_id,
                     SentimentAccountORM.ticker == name).first())
    if dup is not None:
        # Idempotent: if already watching, say so truthfully; don't create a second one (the backend also returns 409 for the same ticker).
        # Placed before confirmation: re-enabling shouldn't first ask the user "confirm?".
        return {"已开通": True, "品牌": name, "account_id": dup.id,
                "note": "这个品牌已经在日常监控里了,没有重复开通。"}

    # ★ The confirmation args fingerprint must match the real execution, so copy the inputs as-is (confirm strips the token itself internally)
    args = {"brand": brand, "keywords": keywords, "aliases": aliases, "run_now": run_now}

    def _preview() -> dict:
        q = quote(db, ctx.deps.user_id, "sentiment_daily", 1)
        return confirm.needs_confirm(
            ctx.deps, tool="start_sentiment_monitor", action_label="开通日常舆情监控",
            args=args,
            preview={"品牌": name, "提示词": keywords or [], "别名": aliases or [],
                     "跑法": "每天自动跑一轮,有负面预警与历史回溯"},
            impact=["从开通起**每天**扣一次积分(一天里跑几轮都只收这一次)",
                    "积分不足只跳过当轮,不会停用"],
            irreversible=[],
            cost={k: v for k, v in q.items() if not k.startswith("_")},
            note="这是会**持续**扣积分的开关,不是一次性动作 —— 必须让用户明确点头再开。",
        )

    if not confirm_token:
        return _preview()
    blocked = confirm.guard(ctx.deps, confirm_token, tool="start_sentiment_monitor",
                            args=args, rebuild_preview=_preview)
    if blocked is not None:
        return blocked

    out = await call_route(
        sa.create_account,
        payload=sa.SentimentAccountCreate(
            # ticker left empty — it used to be required, so the brand name was stuffed in as a placeholder,
            # and every round requested the "fetch by stock code" sources (Eastmoney/Xueqiu) with a Chinese brand name.
            # Now optional: the persisted symbol still falls back to the brand name, and those sources are skipped as a group.
            target=name, ticker="",
            aliases=[a.strip() for a in (aliases or []) if a and a.strip()],
            keywords=[k.strip() for k in (keywords or []) if k and k.strip()],
            run_now=bool(run_now),
        ),
        current_user=_self_actor(ctx), db=db,
    )
    if isinstance(out, dict) and out.get("ok") is False:
        return out                     # 402 insufficient credits and the like, passed through as-is for the model to relay
    return {
        "已开通": True, "品牌": name,
        "account_id": getattr(out, "id", None) or (out or {}).get("id"),
        "note": "从现在起每天自动跑一轮,一天扣一次积分。"
                "首轮结果约 1–5 分钟后出来,用 get_sentiment_today 看。",
    }
