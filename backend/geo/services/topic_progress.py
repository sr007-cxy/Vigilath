"""Which step the topic is at -- the single source of truth for the user-facing "current state + next step".

**Why this module exists**: before this, users only saw one badge, drawn from
`submission_status` (frontend `deriveTopicStatus`) -- that's the **ops review** status,
not "how far along is my monitoring setup". So:

  - words added, not enough money: the page showed 「已通过」 ("approved"), users thought it was running, but not a single cell went out;
  - topic in draft with all 849 words running: the page showed 「草稿」 ("draft");
  - since 2026-09-09 words are no longer reviewed (see api/ai_telemetry.update_topic),
    so the `reviewing` / `rejected` states became dead states that can never be drawn.

So the state is now **derived from facts**, not from some process flag: is there a profile, how many words,
is there enough money for a round, which cell this round has reached. Every step comes with a "what to do next" line and a CTA --
users shouldn't have to guess.

Stages are **ordered**; take the first one not satisfied:
    no_profile → no_queries → insufficient → ready → running → done
`paused` is a cross-cutting switch state taking precedence over everything above (if paused, don't nag about the next step).
`off` (default for new topics since 2026-09-21) = manual mode: stages are computed as usual, only "next step" switches to
the wording "go to the solution page and click generate / add a batch run", and money is reported for "cells that haven't succeeded yet".
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from geo.models.ai_telemetry import (
    AiTelemetryResponseORM,
    AiTelemetryRunORM,
    active_query_texts,
)
from geo.models.balance import BalanceLedgerORM
from geo.services import batch_prepay, topic_schedule

log = logging.getLogger(__name__)

# Stages. The frontend colors / picks CTAs by this key; display copy goes through i18n; don't use label as a criterion.
NO_PROFILE = "no_profile"
NO_QUERIES = "no_queries"
INSUFFICIENT = "insufficient"
READY = "ready"
RUNNING = "running"
DONE = "done"
PAUSED = "paused"
OFF = "off"

_LABEL = {
    NO_PROFILE: "资料没填完",
    NO_QUERIES: "还没有提示词",
    INSUFFICIENT: "积分不够,没开跑",
    READY: "待开跑",
    RUNNING: "监测中",
    DONE: "本轮已完成",
    PAUSED: "已暂停",
    OFF: "已停掉",
}

# kind is for the frontend to pick a button; text is a sentence shown to the user. Don't hardcode a second copy of either in the frontend.
_NEXT = {
    NO_PROFILE: ("edit_profile", "补上品牌资料(公司简称、业务、地域),扩出来的词才准。"),
    NO_QUERIES: ("add_queries", "加提示词:填一个种子词让它扩,或者自己写几条。"),
    INSUFFICIENT: ("recharge", "充值后自动接着跑 —— 不用再来点一次。"),
    READY: ("run", "确认词表没问题就可以开跑;也可以等下一个周期自动跑。"),
    RUNNING: ("wait", "正在问各个引擎,跑完自动出报告,不用守着。"),
    DONE: ("view_report", "去看这一轮的报告。"),
    PAUSED: ("resume", "暂停中,到期自动恢复;也可以立刻恢复。"),
    OFF: ("enable", "持续监测已关掉,想继续就把节奏调回每周或每天。"),
}

# Manual mode (schedule_tier=off, default for new topics since 2026-09-21): no periodic batch runs; runs only when
# 「跑新增」 ("run new") / 「全部跑一轮」 ("run all once") is clicked in the 「监测与跑批」 ("monitoring & batch runs") panel (topic settings -> top of the prompts tab / top of wizard step 4).
# Same stage, but "next step" wording differs from auto mode -- auto mode says
# "wait for the next cycle" / "continues automatically after top-up"; manual mode has no "automatic", and copying it would leave users waiting.
_NEXT_MANUAL = {
    INSUFFICIENT: ("recharge", "充值后在下面点「跑新增」/「全部跑一轮」再跑 —— 手动模式不会自动接上。"),
    READY: ("run", "点「全部跑一轮」开跑;之后加了词,点「跑新增」只补跑新词。想自动跑就把节奏切成每天 / 每周。"),
    DONE: ("view_report", "去看这一轮的报告;加了新词就点「跑新增」。"),
}


def describe(db: Session, topic) -> dict:
    """Which step this topic is at now and what to do next. Read-only, no DB writes."""
    queries = active_query_texts(topic)
    try:
        engines = json.loads(topic.engines_json or "[]")
    except Exception:  # noqa: BLE001
        engines = []
    if not isinstance(engines, list):
        engines = []

    manual = _is_manual(topic)
    # In manual mode a run only covers "not yet succeeded" cells, and money is reported by that count; auto mode runs the whole matrix per round
    cells = (batch_prepay.pending_cells_for_topic(db, topic) if manual
             else len(queries) * len(engines))
    quote = batch_prepay.quote(db, topic, cells)
    affordable = (not quote["chargeable"]
                  or quote["balance_cents"] >= quote["need_cents"])

    run = (db.query(AiTelemetryRunORM)
           .filter(AiTelemetryRunORM.topic_id == topic.id)
           .order_by(AiTelemetryRunORM.id.desc()).first())
    run_block = _run_block(db, run, len(queries) * len(engines))

    stage = _stage(topic, queries, run, affordable)
    kind, text = (_NEXT_MANUAL.get(stage) or _NEXT[stage]) if manual else _NEXT[stage]

    out = {
        "stage": stage,
        "label": _LABEL[stage],
        "next_action": {"kind": kind, "text": text},
        "queries": {
            "active": len(queries),
            # total includes soft-deleted and unticked words -- users seeing "800 of 849 words running" won't
            # think the rest were lost.
            "total": _total_queries(topic),
        },
        "engines": len(engines),
        # The user came in from the "choose seed words" step. If the status page only reports "800 words running", they can't
        # match the few seeds they picked to these hundreds of words -- include the seed word surfaces so the frontend can lay them out.
        "seeds": _seed_texts(topic),
        "cost": {
            "cells": quote["cells"],
            "price_cents": quote["price_cents"],
            "need_cents": quote["need_cents"],
            "balance_cents": quote["balance_cents"],
            "chargeable": quote["chargeable"],
            "affordable": affordable,
        },
        "run": run_block,
        "schedule": {
            "tier": topic_schedule.normalize_tier(getattr(topic, "schedule_tier", None)),
            "enabled": bool(getattr(topic, "enabled", False)),
            "paused_until": _iso(getattr(topic, "paused_until", None)),
            # Manual mode: no periodic batch runs; runs only when the button is clicked on the solution page (only cells that haven't succeeded)
            "manual": manual,
        },
    }
    return out


def _is_manual(topic) -> bool:
    tier = topic_schedule.normalize_tier(getattr(topic, "schedule_tier", None))
    return tier == "off" or not getattr(topic, "enabled", False)


def _stage(topic, queries: list[str], run, affordable: bool) -> str:
    # While paused, don't nag about "next step"; the user pressed it themselves.
    # tier=off used to short-circuit to OFF ("stopped") here too -- since 2026-09-21 off is the default for new topics
    # (manual mode), not "stopped": it should go through normal stages, with "next step" switched to manual-mode wording
    # (see _NEXT_MANUAL).
    paused_until = getattr(topic, "paused_until", None)
    if paused_until is not None and not _is_manual(topic):
        return PAUSED

    if not _has_profile(topic):
        return NO_PROFILE
    if not queries:
        return NO_QUERIES
    if run is not None and (run.status or "") == "running":
        return RUNNING
    # Insufficient money ranks before ready: words in place but not a single cell sent for lack of money is the state most easily misread as
    # "running" -- in the incident (topic 32) that's exactly what the user assumed.
    if not affordable:
        return INSUFFICIENT
    if run is not None and (run.status or "") in ("success", "failed"):
        return DONE
    return READY


def _has_profile(topic) -> bool:
    """Whether the profile counts as filled -- a short name or profile name is enough. Lenient rather than strict: this step is only guidance,
    and being too strict would mark every old topic (which never went through the profile form) as "profile incomplete"."""
    try:
        p = json.loads(getattr(topic, "profile_json", "") or "{}")
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(p, dict):
        return False
    if any(str(p.get(k) or "").strip()
           for k in ("company_short_name", "profile_name", "core_service_overview")):
        return True
    # Old topics have no profile, but a filled target is enough to run -- don't block
    return bool(str(getattr(topic, "target", "") or "").strip())


def _seed_texts(topic) -> list[str]:
    """This topic's seed word surfaces (in persistence order, deduplicated). No longer filtered by status -- words aren't reviewed anymore,
    and old pending rows in the DB run just the same; filtering by status would hide them from the page."""
    try:
        raw = json.loads(getattr(topic, "seed_prompts_json", "") or "[]")
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = item.get("text") if isinstance(item, dict) else item
        text = str(text or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _total_queries(topic) -> int:
    try:
        raw = json.loads(getattr(topic, "queries_json", "") or "[]")
    except Exception:  # noqa: BLE001
        return 0
    return sum(1 for q in raw if (isinstance(q, dict) and q.get("text")) or isinstance(q, str))


def _run_block(db: Session, run, cells_total: int) -> Optional[dict]:
    if run is None:
        return None
    engines = _engine_breakdown(db, run.id)
    done = sum(e["ok"] + e["error"] for e in engines)
    total = max(cells_total, done)          # if the word list shrank mid-run, trust the actual run count; don't show 120%
    return {
        "id": run.id,
        "status": run.status or "",
        # For a round killed by insufficient balance, the error cause must be passed to the user honestly -- otherwise the page just shows
        # "cancelled", and nobody would guess it was about money.
        "error": run.error or None,
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.finished_at),
        "cells_done": done,
        "cells_total": total,
        "percent": int(done * 100 / total) if total else 0,
        # How much this round really took from the balance -- the only "result" users care about. Batch runs **charge first, refund later**
        # (batch_prepay), so net = prepay - close-out refund; the old post-paid path writes the same pair
        # (ref_type='run', ref_id), included together so the frontend needn't tell which path was used.
        **_run_money(db, run.id),
        # "What ran" -- actual run counts split by engine. A total can't answer what users are really asking:
        # which of the five engines actually responded. When one engine errors for the whole round (rate limit / offline), overall progress
        # is still 100%; only this breakdown exposes it.
        "engines": engines,
    }


def _run_money(db: Session, run_id: int) -> dict:
    """This round's money: charged, refunded, net charged (unit: credits).

    Paired by `(ref_type='run', ref_id)`, **not filtered by kind** -- the refund entry is recorded as KIND_REFUND
    rather than KIND_BATCH (see topic_billing.refund for why); filtering by kind leaves only the prepay entry,
    and the page would report a high price that never actually happened.
    """
    row = db.query(
        func.sum(case((BalanceLedgerORM.direction == "debit",
                       BalanceLedgerORM.amount_cents), else_=0)),
        func.sum(case((BalanceLedgerORM.direction == "credit",
                       BalanceLedgerORM.amount_cents), else_=0)),
    ).filter(
        BalanceLedgerORM.ref_type == "run",
        BalanceLedgerORM.ref_id == str(int(run_id)),
    ).first()
    debit = int((row[0] if row else 0) or 0)
    credit = int((row[1] if row else 0) or 0)
    return {
        "charged_cents": debit,
        "refunded_cents": credit,
        "net_cents": max(0, debit - credit),
    }


def _engine_breakdown(db: Session, run_id: int) -> list[dict]:
    """How many cells each engine was asked this round and how many succeeded. Sorted by ok descending so the frontend can render directly."""
    rows = (db.query(AiTelemetryResponseORM.engine,
                     func.count(AiTelemetryResponseORM.id),
                     func.sum(_error_flag()))
            .filter(AiTelemetryResponseORM.run_id == run_id)
            .group_by(AiTelemetryResponseORM.engine)
            .all())
    out = []
    for engine, total, errs in rows:
        total = int(total or 0)
        errs = int(errs or 0)
        out.append({"engine": engine or "", "ok": total - errs, "error": errs})
    out.sort(key=lambda e: (-e["ok"], e["engine"]))
    return out


def _error_flag():
    """1 = this response is an error. error may be NULL or an empty string; both count as success."""
    col = AiTelemetryResponseORM.error
    return case((col.isnot(None) & (col != ""), 1), else_=0)


def _iso(v) -> Optional[str]:
    return v.isoformat() if v is not None else None
