"""Guardrails for spending money in conversation — the caps layer.

**History**: this originally said "the product has decided 'paid media publishing needs no second confirmation; publish if the balance suffices'",
and this module was meant to **replace** confirm.py. Product testing on 2026-08-30 overturned that decision — the most common
user complaint was "credit-consuming services run automatically with no notice; credits are charged even when interrupted".
So the customer line's money-spending tools now have **two layers**:

  · `confirm.py`'s two-step confirmation (HMAC ticket + turn binding + args fingerprint) — blocks "the model acting on its own",
    the only mechanism guaranteeing "the user saw the amount with their own eyes and nodded";
  · this module's caps — blocks "a large sum confirmed away repeatedly within a day".

**Neither replaces the other**; new money-spending tools must pass both. The caps ask the user nothing and just bound the worst case
to a tolerable number; defaults are fairly generous, and in an incident lowering the env tightens them immediately without a release.

**The idempotency key excludes turn_id.** Deliberately the opposite of `confirm.py`: there `turn_id` guarantees "confirmation requires a genuinely
new user message", while here we want the opposite property — the model retrying the same order must hit the same key;
including turn_id would mean "charged once per retry".

What really stops prompt injection isn't here; it's that **the money-spending tools' parameters can't be fabricated**: `pay_and_publish` only accepts
`task_ids`, and tasks must first be created by `push_media_draft` / `auto_select_media` under this account's topic
— injected text can't invent a valid task_id; nor is the amount given by the model; it is always determined by live server-side pricing.
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

from agents.geo import gates

log = logging.getLogger(__name__)

_DEFAULTS = {
    "AGENT_SPEND_MAX_SINGLE_CENTS": 20000,     # Per transaction ¥200
    "AGENT_SPEND_MAX_DAILY_CENTS": 100000,     # Per day ¥1000
    "AGENT_SPEND_MAX_DAILY_COUNT": 20,         # Transactions per day
    "AGENT_SPEND_COOLDOWN_S": 60,              # Cooldown for the same order
}

# Written into balance_ledger.note_json.via — the sole counting criterion of the daily gates,
# and the reconciliation basis for "which money was spent by the agent" afterwards.
#
# ⚠️ This tag **must really be written**, otherwise gates ④⑤ always count 0 and never block anything.
#   The places that really spend money (rwj_channel._publish_one etc.) don't know who triggered them,
#   so this module's `marked()` wraps a context around the call and balance_service stamps it itself.
#   **When adding money-spending tools, wrap the actual order step in marked()**; missing it means these two gates don't exist.
#   (Prior case of the same kind: commit 4f717dd0 "counter source empty, the gate was inert" was reverted entirely.)
LEDGER_TAG = os.environ.get("AGENT_SPEND_LEDGER_TAG", "agent")

# (tool, ref_id) -> last result. Repeat calls within the cooldown return the last result instead of really placing a second order.
_recent: dict[tuple[str, str], tuple[float, dict]] = {}


def _num(name: str) -> int:
    try:
        return max(0, int(os.environ.get(name, _DEFAULTS[name])))
    except (TypeError, ValueError):
        return _DEFAULTS[name]


def dry_run() -> bool:
    """=1 runs the full chain without charging or going out to the network; the return body is marked dry_run. Required for canary rollout."""
    return (os.environ.get("AGENT_SPEND_DRY_RUN", "0") or "").strip().lower() \
        in ("1", "true", "yes")


def _points(c: int) -> str:
    """Credit display. 1 CNY = 100 credits = the "cents" in the DB, 1:1 with no division."""
    return f"{int(c or 0)}"


@dataclass(frozen=True)
class SpendPlan:
    tool: str
    amount_cents: int
    ref_id: str
    cap: str = "publish_paid"       # Which capability gate to use


def _blocked(code: str, message: str, **extra) -> dict:
    return {"blocked": True, "modified": False, "code": code, "note": message, **extra}


def precheck(deps, plan: SpendPlan) -> Optional[dict]:
    """Six gates in fixed order. Returns None when passed, a structured dict when blocked (the caller returns it as-is)."""
    # ① Capability tier (channel + caps)
    hit = gates.require(deps, plan.cap)
    if hit is not None:
        return hit

    # ② Action allowlist + frequency (internal unlimited tiers pass, but audits are still written)
    from agents.geo.methods import usage_guardrail_check

    try:
        usage_guardrail_check(deps, plan.tool)
    except Exception as e:  # noqa: BLE001 — CallDenied and the like
        return _blocked("RATE_LIMITED", f"调用被限流,请稍后再试({e})")

    amount = int(plan.amount_cents or 0)

    # ③ Per-transaction cap
    cap = _num("AGENT_SPEND_MAX_SINGLE_CENTS")
    if cap and amount > cap:
        return _blocked(
            "SPEND_LIMIT_SINGLE",
            f"对话里单笔最多 {_points(cap)} 积分,这一单要 {_points(amount)} 积分。"
            f"更大的金额请到网页的发布中心操作。",
            limit_cents=cap, amount_cents=amount,
        )

    # ④⑤ Daily amount / count — only counts what the agent spent (note_json.via='agent')
    from geo.services import balance_service

    db = getattr(deps, "db", None)
    if db is not None:
        daily = _num("AGENT_SPEND_MAX_DAILY_CENTS")
        if daily:
            spent = balance_service.spent_today_cents(db, deps.user_id, via=LEDGER_TAG)
            if spent + amount > daily:
                return _blocked(
                    "SPEND_LIMIT_DAILY",
                    f"今天在对话里已经花了 {_points(spent)} 积分,单日上限 "
                    f"{_points(daily)} 积分,这一单 {_points(amount)} 积分会超出。"
                    f"明天可以继续,或到网页端操作。",
                    limit_cents=daily, spent_today_cents=spent,
                )
        cnt_cap = _num("AGENT_SPEND_MAX_DAILY_COUNT")
        if cnt_cap:
            n = _count_today(db, deps.user_id)
            if n >= cnt_cap:
                return _blocked(
                    "SPEND_LIMIT_COUNT",
                    f"今天在对话里已经下了 {n} 单,单日上限 {cnt_cap} 单。",
                    limit_count=cnt_cap, count_today=n,
                )

    # ⑥ Balance pre-check — offer "go top up" one step early instead of a 402 at actual order time
    if db is not None and amount > 0:
        if not balance_service.can_afford(db, deps.user_id, amount):
            bal = balance_service.get_balance(db, deps.user_id)
            return _blocked(
                "INSUFFICIENT_BALANCE",
                f"这一单需要 {_points(amount)} 积分,当前积分 {_points(bal)}。"
                f"到「账户 → 我的积分」充值后就能继续。",
                required_cents=amount, balance_cents=bal,
                topup={"web": "/account/balance"},
            )
    return None


def _count_today(db, user_id: int) -> int:
    from geo.models.balance import BalanceLedgerORM
    from geo.services import balance_service

    return db.query(BalanceLedgerORM.id).filter(
        BalanceLedgerORM.user_id == user_id,
        BalanceLedgerORM.bj_date == balance_service._bj_date(),
        BalanceLedgerORM.note_json.like(f'%"via": "{LEDGER_TAG}"%'),
    ).count()


def cooled_down(plan: SpendPlan) -> Optional[dict]:
    """The same order called repeatedly within the cooldown — return the last result, don't really place a second order.

    Model retries, user double-clicks and SSE reconnects all produce "the same sentence run twice", and orders are irreversible.
    The idempotency key is the server-side fallback; this is an upstream interception that saves a wasted run.
    """
    window = _num("AGENT_SPEND_COOLDOWN_S")
    if not window:
        return None
    key = (plan.tool, plan.ref_id)
    hit = _recent.get(key)
    if hit and time.time() - hit[0] < window:
        return {**hit[1], "note": "刚刚已经提交过同一单,这里给的是上次的结果,没有重复下单。"}
    return None


def remember(plan: SpendPlan, result: dict) -> dict:
    _recent[(plan.tool, plan.ref_id)] = (time.time(), result)
    return result


@contextmanager
def marked():
    """Wrap the step that really spends money so the ledger entry carries `via=agent`.

    Without it, `precheck`'s daily-amount / daily-count gates are just decoration — they only count
    ledger entries carrying this tag.
    """
    from geo.services import balance_service

    with balance_service.via(LEDGER_TAG):
        yield


def idem_key(deps, plan: SpendPlan) -> str:
    """Idempotency key for when the agent charges money itself. **Excludes turn_id** — see the module header.

    Note that none of the current money-spending tools use it: `pay_and_publish` delegates charging to
    `rwj_channel._publish_one`, whose idempotency relies on the key generated there from `rwjpub:{task_id}` +
    this module's cooldown. **Use this key only when adding a tool that calls charge() directly in the agent process**;
    don't force it into a chain that already has server-side idempotency just to "look idempotent".
    """
    return f"agent:{plan.tool}:{deps.account_id}:{plan.ref_id}"
