"""vscl_rl -- Vigilath-Siyuan closed-loop reinforcement learning algorithm (VSCL-RL, Vigilath-Siyuan Closed-Loop RL).

Models GEO optimization as a "state-action-reward-policy" closed loop (patent P-16,
docs/09-软著与专利/专利申请文档-威吉力思源生成引擎优化强化学习算法.md):

  State   ai_telemetry_response_detail details (query x engine x day, hit/rank/negative/self-citation)
  Action  arm = (query, creative direction, copy type); pulling an arm = generating placement content for that query with that combination
  Reward  difference of observation window vs baseline window: delta hit rate + delta top3 rate + sparse first-hit reward + count of own content
          cited - delta negative rate - action cost; subject to **snapshot consistency** (word pack / profile fingerprint changes
          -> action voided as stale, not credited to the arm), eliminating non-stationary contamination
  Policy  UCB1 + gap prior (queries with 0 hit rate get the highest exploration bonus, same greedy metric definition as admin_review
          `_priority_rank`) + epsilon-greedy random exploration; unpulled arms initialized optimistically

Action lifecycle: planned (selected by this module) -> executed (content generated, doc linked, auto
reconciled or via record_execution) -> rewarded / stale. Generation and publishing still use the existing pipelines
(content_generator / content_scheduler / agent publish guardrails); this module only does
"which arm to pick" and "settle effect back to the arm", never sending content out directly.

CLI (manual or cron during testing; run alembic upgrade head first to create tables):
  python -m geo.services.vscl_rl --topic 18                 # one tick: settle + pick arms (dry-run prints)
  python -m geo.services.vscl_rl --topic 18 --commit        # same, planned actions persisted to DB
  python -m geo.services.vscl_rl --topic 18 --settle-only   # only settle due actions
  python -m geo.services.vscl_rl --topic 18 --report        # arm stats report
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from geo.api.ai_telemetry_reports import _bj_date
from geo.database import SessionLocal
from geo.models.ai_telemetry import (
    AiTelemetryQueryHitORM, AiTelemetryResponseDetailORM, AiTelemetryTopicORM,
    TopicGeneratedDocORM,
)
from geo.models.vscl_rl import VsclRlActionORM, VsclRlArmORM
from geo.services.content_generator import DIRECTION_HINTS, TYPE_HINTS

# ───────────────────────── Tunable parameters (env overrides) ─────────────────────────

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default

def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default

LAG_DAYS = _env_int("GEO_VSCL_LAG_DAYS", 3)            # indexing delay from placement -> observable by engines
WINDOW_DAYS = _env_int("GEO_VSCL_WINDOW_DAYS", 7)      # reward observation window length
BASELINE_DAYS = _env_int("GEO_VSCL_BASELINE_DAYS", 7)  # baseline window length when picking arms
TICK_K = _env_int("GEO_VSCL_TICK_K", 3)                # arms picked per tick
EPSILON = _env_float("GEO_VSCL_EPSILON", 0.1)          # epsilon-greedy random exploration probability
UCB_C = _env_float("GEO_VSCL_UCB_C", 0.6)              # UCB confidence term coefficient (rewards normalized to [0,1])
OPTIMISTIC_Q = _env_float("GEO_VSCL_OPTIMISTIC_Q", 0.8)  # optimistic initial value for unpulled arms

# Composite reward weights: rate differences in percentage points, sparse terms in counts
REWARD_W = {
    "hit": 0.6,        # delta hit rate (pp)
    "top3": 0.3,       # delta top3 rate (pp)
    "first_hit": 8.0,  # engine cells with a new first hit for this query in the observation window
    "cite": 6.0,       # number of docs produced by this action that AI cited
    "neg": 0.8,        # delta negative rate (pp, penalty)
    "cost": 0.5,       # action cost per piece of content
}

def _combo_space() -> list[tuple[str, str]]:
    """Action combination space: creative direction x copy type. By default only text types (video voice-over scripts aren't auto-placed)."""
    dirs = [d.strip() for d in os.environ.get("GEO_VSCL_DIRECTIONS", "").split(",") if d.strip()] \
        or list(DIRECTION_HINTS.keys())
    types = [t.strip() for t in os.environ.get("GEO_VSCL_TYPES", "").split(",") if t.strip()] \
        or ["long_form", "medium_post", "faq_list"]
    dirs = [d for d in dirs if d in DIRECTION_HINTS]
    types = [t for t in types if t in TYPE_HINTS]
    return [(d, t) for d in dirs for t in types]


# ───────────────────────── State reading ─────────────────────────

def _monitored_queries(topic: AiTelemetryTopicORM) -> list[str]:
    """Monitored query set: selected + approved + not soft-deleted; old data without a selected flag falls back to all.

    ★ Soft deletes change this set -> context_fingerprint changes -> all in-flight actions for the topic go stale and aren't credited.
      This is correct (the word pack changed, so the difference baseline is invalid), but has an ops cost; the delete-word endpoint copy should warn about it.
    """
    try:
        items = json.loads(topic.queries_json or "[]")
    except Exception:  # noqa: BLE001
        return []
    items = [it for it in items if not (isinstance(it, dict) and it.get("deleted_at"))]
    picked: list[str] = []
    for it in items:
        if isinstance(it, dict):
            if it.get("selected") and it.get("status") in (None, "", "approved"):
                text = (it.get("text") or "").strip()
                if text:
                    picked.append(text)
    if not picked:
        for it in items:
            text = it if isinstance(it, str) else (it.get("text") or "") if isinstance(it, dict) else ""
            text = str(text).strip()
            if text:
                picked.append(text)
    return picked


def context_fingerprint(topic: AiTelemetryTopicORM) -> str:
    """Context fingerprint -- the criterion for the snapshot consistency constraint.

    Any change to the word pack (monitored set) or brand profile -> fingerprint changes -> old actions' difference baseline is invalid,
    marked stale at settlement and not credited, preventing "config changes misjudged as optimization effects".
    """
    payload = json.dumps({
        "target": topic.target or "",
        "aliases": topic.target_aliases_json or "[]",
        "queries": sorted(_monitored_queries(topic)),
        "profile": hashlib.sha1((topic.profile_json or "{}").encode("utf-8")).hexdigest(),
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _window_stats(db: Session, topic_id: int, queries: list[str],
                  start_day: str, end_day: str) -> dict[str, dict]:
    """Window aggregation of response_detail: per query {runs, hits, top3, neg, cited}."""
    out: dict[str, dict] = {q: {"runs": 0, "hits": 0, "top3": 0, "neg": 0, "cited": 0} for q in queries}
    if not queries:
        return out
    rows = (
        db.query(AiTelemetryResponseDetailORM)
          .filter(
              AiTelemetryResponseDetailORM.topic_id == topic_id,
              AiTelemetryResponseDetailORM.day >= start_day,
              AiTelemetryResponseDetailORM.day <= end_day,
              AiTelemetryResponseDetailORM.is_valid.is_(True),
              AiTelemetryResponseDetailORM.query.in_(queries),
          )
          .all()
    )
    for r in rows:
        agg = out.get(r.query)
        if agg is None:
            continue
        agg["runs"] += 1
        if r.hit:
            agg["hits"] += 1
        if r.is_top3:
            agg["top3"] += 1
        if r.is_negative:
            agg["neg"] += 1
        if r.cites_owned:
            agg["cited"] += 1
    return out


def _rates(agg: dict) -> dict[str, float]:
    runs = agg.get("runs") or 0
    if runs <= 0:
        return {"hit": 0.0, "top3": 0.0, "neg": 0.0, "cited": 0.0, "runs": 0}
    return {
        "hit": agg["hits"] / runs * 100,
        "top3": agg["top3"] / runs * 100,
        "neg": agg["neg"] / runs * 100,
        "cited": agg["cited"] / runs * 100,
        "runs": runs,
    }


def _gap_bonus(db: Session, topic_id: int, queries: list[str]) -> dict[str, float]:
    """Gap prior -- exploration bonus with the same metric definition as admin_review._priority_rank.

    Queries with 0 lifetime hit rate (or never run) are the biggest visibility gaps, +0.30;
    weak queries <50% +0.15; others 0. Added on top of the UCB score so the policy attacks gaps first.
    """
    cells = (
        db.query(AiTelemetryQueryHitORM)
          .filter(AiTelemetryQueryHitORM.topic_id == topic_id)
          .all()
    )
    agg: dict[str, dict] = {}
    for c in cells:
        a = agg.setdefault(c.query, {"runs": 0, "hits": 0})
        a["runs"] += c.total_runs or 0
        a["hits"] += c.total_hits or 0
    out: dict[str, float] = {}
    for q in queries:
        a = agg.get(q, {"runs": 0, "hits": 0})
        if a["runs"] <= 0 or a["hits"] <= 0:
            out[q] = 0.30
        elif a["hits"] / a["runs"] * 100 < 50:
            out[q] = 0.15
        else:
            out[q] = 0.0
    return out


# ───────────────────────── Arms and actions ─────────────────────────

def _arm_key(query: str, direction: str, copywriting_type: str) -> str:
    return hashlib.sha1(f"{query}|{direction}|{copywriting_type}".encode("utf-8")).hexdigest()


def _get_or_create_arm(db: Session, topic_id: int, query: str,
                       direction: str, copywriting_type: str) -> VsclRlArmORM:
    key = _arm_key(query, direction, copywriting_type)
    arm = (
        db.query(VsclRlArmORM)
          .filter(VsclRlArmORM.topic_id == topic_id, VsclRlArmORM.arm_key == key)
          .first()
    )
    if arm is None:
        arm = VsclRlArmORM(topic_id=topic_id, arm_key=key, query=query,
                           direction=direction, copywriting_type=copywriting_type)
        db.add(arm)
        db.flush()
    return arm


def select_actions(db: Session, topic: AiTelemetryTopicORM, *,
                   k: int = TICK_K, epsilon: float = EPSILON) -> list[VsclRlActionORM]:
    """Policy layer: UCB1 + gap prior + epsilon-greedy; pick k arms to pull this round and create planned actions.

    At most 1 arm per query per tick (spread budget across multiple gaps rather than producing k articles in a row
    for one query); unpulled arms use optimistic initial values to guarantee systematic exploration.
    """
    queries = _monitored_queries(topic)
    if not queries:
        return []
    combos = _combo_space()
    if not combos:
        return []
    bonus = _gap_bonus(db, topic.id, queries)

    arms = {
        a.arm_key: a for a in
        db.query(VsclRlArmORM).filter(VsclRlArmORM.topic_id == topic.id).all()
    }
    total_pulls = sum(a.pulls for a in arms.values()) + 1

    # Candidate scoring: (score, query, direction, type)
    candidates: list[tuple[float, str, str, str]] = []
    for q in queries:
        for d, t in combos:
            key = _arm_key(q, d, t)
            arm = arms.get(key)
            # Deterministic micro-jitter: a stable tie-break order among equal-score arms, so we don't pick the lexicographically first every round
            jitter = int(key[:8], 16) / 0xFFFFFFFF * 1e-3
            if arm is None or arm.pulls <= 0:
                score = OPTIMISTIC_Q + bonus[q] + jitter
            else:
                mean = arm.reward_norm_sum / arm.pulls
                explore = UCB_C * math.sqrt(2 * math.log(total_pulls) / arm.pulls)
                score = mean + explore + bonus[q] + jitter
            candidates.append((score, q, d, t))

    candidates.sort(key=lambda c: -c[0])

    picked: list[tuple[float, str, str, str]] = []
    used_queries: set[str] = set()
    pool = candidates[:]
    while pool and len(picked) < k:
        if random.random() < epsilon:
            idx = random.randrange(len(pool))
        else:
            idx = 0
        cand = pool.pop(idx)
        if cand[1] in used_queries:
            continue
        used_queries.add(cand[1])
        picked.append(cand)

    # Baseline window: BASELINE_DAYS days up to yesterday (Beijing day)
    today_bj = _bj_date(datetime.utcnow())
    base_end = today_bj - timedelta(days=1)
    base_start = base_end - timedelta(days=BASELINE_DAYS - 1)
    baseline = _window_stats(db, topic.id, [c[1] for c in picked],
                             base_start.isoformat(), base_end.isoformat())
    fp = context_fingerprint(topic)

    actions: list[VsclRlActionORM] = []
    for score, q, d, t in picked:
        arm = _get_or_create_arm(db, topic.id, q, d, t)
        action = VsclRlActionORM(
            topic_id=topic.id, arm_id=arm.id, status="planned",
            context_fingerprint=fp,
            baseline_json=json.dumps(baseline.get(q, {}), ensure_ascii=False),
            note=f"score={score:.4f}",
        )
        db.add(action)
        actions.append(action)
    db.flush()
    return actions


def record_execution(db: Session, action_id: int, doc_ids: list[int]) -> Optional[VsclRlActionORM]:
    """Execution receipt: content generated/placed for the arm combination; record the doc on the action (the ledger for reward attribution)."""
    action = db.get(VsclRlActionORM, action_id)
    if action is None or action.status not in ("planned", "executed"):
        return None
    known = set(json.loads(action.doc_ids_json or "[]"))
    known.update(int(i) for i in doc_ids)
    action.doc_ids_json = json.dumps(sorted(known))
    action.status = "executed"
    if action.executed_at is None:
        action.executed_at = datetime.utcnow()
    db.flush()
    return action


def _auto_attach_docs(db: Session, topic: AiTelemetryTopicORM) -> int:
    """Auto reconciliation: planned action <- the doc whose generation the action triggered.

    Prefer exact reconciliation by explicit binding TopicGeneratedDocORM.vscl_action_id == action.id
    (written directly by siyuan_tick at generation, stable and traceable on the new path); if not found fall back to the old
    (source_query_text, creation_direction, copywriting_type, created_at, source)
    coordinate matching -- compatible with data from before vscl_action_id launched and generations not triggered by siyuan.
    """
    planned = (
        db.query(VsclRlActionORM)
          .filter(VsclRlActionORM.topic_id == topic.id, VsclRlActionORM.status == "planned")
          .all()
    )
    attached = 0
    for action in planned:
        arm = db.get(VsclRlArmORM, action.arm_id)
        if arm is None:
            continue
        # 1) Explicit binding first
        docs = (
            db.query(TopicGeneratedDocORM.id)
              .filter(
                  TopicGeneratedDocORM.topic_id == topic.id,
                  TopicGeneratedDocORM.vscl_action_id == action.id,
              )
              .all()
        )
        # 2) Fall back to coordinate matching (old data / not triggered by siyuan)
        if not docs:
            docs = (
                db.query(TopicGeneratedDocORM.id)
                  .filter(
                      TopicGeneratedDocORM.topic_id == topic.id,
                      TopicGeneratedDocORM.vscl_action_id.is_(None),
                      TopicGeneratedDocORM.source_query_text == arm.query,
                      TopicGeneratedDocORM.creation_direction == (arm.direction or None),
                      TopicGeneratedDocORM.copywriting_type == (arm.copywriting_type or None),
                      TopicGeneratedDocORM.created_at >= action.planned_at,
                      TopicGeneratedDocORM.source == "ai",
                  )
                  .all()
            )
        if docs:
            record_execution(db, action.id, [d.id for d in docs])
            attached += 1
    return attached


# ───────────────────────── Reward settlement ─────────────────────────

def _settle_one(db: Session, topic: AiTelemetryTopicORM, action: VsclRlActionORM,
                fp_now: str) -> str:
    """Settle a single action: returns rewarded / stale / skipped."""
    arm = db.get(VsclRlArmORM, action.arm_id)
    if arm is None:
        action.status = "stale"
        action.note = (action.note or "") + " |arm-missing"
        return "stale"

    # Snapshot consistency constraint: word pack/profile changed -> difference baseline invalid, voided and not credited
    if action.context_fingerprint and action.context_fingerprint != fp_now:
        action.status = "stale"
        action.rewarded_at = datetime.utcnow()
        action.note = (action.note or "") + " |fingerprint-changed"
        return "stale"

    exec_day = _bj_date(action.executed_at or action.planned_at)
    obs_start = exec_day + timedelta(days=LAG_DAYS)
    obs_end = obs_start + timedelta(days=WINDOW_DAYS - 1)
    today_bj = _bj_date(datetime.utcnow())
    if today_bj <= obs_end:                       # observation window not closed
        return "skipped"

    post = _rates(_window_stats(db, topic.id, [arm.query],
                                obs_start.isoformat(), obs_end.isoformat())[arm.query])
    if post["runs"] <= 0:                         # engines didn't run this query in the window; no observation
        action.status = "stale"
        action.rewarded_at = datetime.utcnow()
        action.note = (action.note or "") + " |no-observation"
        return "stale"
    try:
        base = _rates(json.loads(action.baseline_json or "{}"))
    except Exception:  # noqa: BLE001
        base = _rates({})

    doc_ids = [int(i) for i in json.loads(action.doc_ids_json or "[]")]
    cited_docs = 0
    if doc_ids:
        for doc in db.query(TopicGeneratedDocORM).filter(TopicGeneratedDocORM.id.in_(doc_ids)).all():
            try:
                cited = json.loads(doc.cited_by_json or "{}")
            except Exception:  # noqa: BLE001
                cited = {}
            if any(v for v in cited.values()):
                cited_docs += 1

    first_hits = (
        db.query(AiTelemetryQueryHitORM)
          .filter(
              AiTelemetryQueryHitORM.topic_id == topic.id,
              AiTelemetryQueryHitORM.query == arm.query,
              AiTelemetryQueryHitORM.first_hit_at.isnot(None),
          )
          .all()
    )
    first_hit_cells = sum(
        1 for c in first_hits if obs_start <= _bj_date(c.first_hit_at) <= obs_end
    )

    detail = {
        "d_hit_pp": post["hit"] - base["hit"],
        "d_top3_pp": post["top3"] - base["top3"],
        "d_neg_pp": post["neg"] - base["neg"],
        "first_hit_cells": first_hit_cells,
        "cited_docs": cited_docs,
        "cost_docs": len(doc_ids),
        "base_runs": base["runs"], "post_runs": post["runs"],
        "window": [obs_start.isoformat(), obs_end.isoformat()],
    }
    reward = (
        REWARD_W["hit"] * detail["d_hit_pp"]
        + REWARD_W["top3"] * detail["d_top3_pp"]
        + REWARD_W["first_hit"] * first_hit_cells
        + REWARD_W["cite"] * cited_docs
        - REWARD_W["neg"] * detail["d_neg_pp"]
        - REWARD_W["cost"] * len(doc_ids)
    )
    reward_norm = max(0.0, min(1.0, 0.5 + reward / 100.0))

    action.reward = round(reward, 4)
    action.reward_norm = round(reward_norm, 6)
    action.reward_detail_json = json.dumps(detail, ensure_ascii=False)
    action.status = "rewarded"
    action.rewarded_at = datetime.utcnow()

    arm.pulls += 1
    arm.reward_sum += reward
    arm.reward_norm_sum += reward_norm
    arm.reward_sq_sum += reward_norm * reward_norm
    arm.last_reward = round(reward, 4)
    arm.last_pulled_at = datetime.utcnow()
    return "rewarded"


def settle_rewards(db: Session, topic: AiTelemetryTopicORM) -> dict[str, int]:
    """Settle all due executed actions, crediting rewards to arms; fingerprint changes / no observation are marked stale."""
    fp_now = context_fingerprint(topic)
    stats = {"rewarded": 0, "stale": 0, "skipped": 0}
    actions = (
        db.query(VsclRlActionORM)
          .filter(VsclRlActionORM.topic_id == topic.id, VsclRlActionORM.status == "executed")
          .order_by(VsclRlActionORM.id.asc())
          .all()
    )
    for action in actions:
        stats[_settle_one(db, topic, action, fp_now)] += 1
    db.flush()
    return stats


# ───────────────────────── Closed-loop tick and report ─────────────────────────

def run_tick(db: Session, topic_id: int, *, k: int = TICK_K,
             epsilon: float = EPSILON) -> dict:
    """One complete closed-loop iteration: reconcile executions -> settle rewards -> pick next round's arms (planned).

    Generation/placement itself is done by existing pipelines (execution plan / content_scheduler / agent);
    this function sends no content out and can safely run repeatedly.
    """
    topic = db.get(AiTelemetryTopicORM, topic_id)
    if topic is None:
        raise ValueError(f"topic {topic_id} 不存在")
    attached = _auto_attach_docs(db, topic)
    settled = settle_rewards(db, topic)
    actions = select_actions(db, topic, k=k, epsilon=epsilon)
    plan = []
    for a in actions:
        arm = db.get(VsclRlArmORM, a.arm_id)
        plan.append({
            "action_id": a.id, "query": arm.query,
            "direction": arm.direction, "copywriting_type": arm.copywriting_type,
            "pulls": arm.pulls, "note": a.note,
        })
    return {"attached": attached, "settled": settled, "planned": plan}


def policy_report(db: Session, topic_id: int, limit: int = 30) -> list[dict]:
    """Arm stats report: sorted by mean normalized reward (only pulled arms listed)."""
    arms = (
        db.query(VsclRlArmORM)
          .filter(VsclRlArmORM.topic_id == topic_id, VsclRlArmORM.pulls > 0)
          .all()
    )
    rows = []
    for a in arms:
        rows.append({
            "query": a.query, "direction": a.direction, "type": a.copywriting_type,
            "pulls": a.pulls,
            "mean_reward": round(a.reward_sum / a.pulls, 3),
            "mean_norm": round(a.reward_norm_sum / a.pulls, 4),
            "last_reward": a.last_reward,
        })
    rows.sort(key=lambda r: -r["mean_norm"])
    return rows[:limit]


# ───────────────────────── CLI ─────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="VSCL-RL 闭环强化学习 tick")
    parser.add_argument("--topic", type=int, required=True)
    parser.add_argument("--commit", action="store_true", help="落库(默认 dry-run 回滚)")
    parser.add_argument("--settle-only", action="store_true", help="只结算不选臂")
    parser.add_argument("--report", action="store_true", help="打印臂统计报表")
    parser.add_argument("--k", type=int, default=TICK_K)
    parser.add_argument("--epsilon", type=float, default=EPSILON)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.report:
            for row in policy_report(db, args.topic):
                print(json.dumps(row, ensure_ascii=False))
            return 0
        topic = db.get(AiTelemetryTopicORM, args.topic)
        if topic is None:
            print(f"topic {args.topic} 不存在", file=sys.stderr)
            return 1
        if args.settle_only:
            attached = _auto_attach_docs(db, topic)
            result = {"attached": attached, "settled": settle_rewards(db, topic)}
        else:
            result = run_tick(db, args.topic, k=args.k, epsilon=args.epsilon)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.commit:
            db.commit()
            print("已落库", file=sys.stderr)
        else:
            db.rollback()
            print("dry-run,已回滚(--commit 落库)", file=sys.stderr)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
