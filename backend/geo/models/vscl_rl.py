"""VSCL-RL (Vigilath-Siyuan Closed-Loop Reinforcement Learning) -- ORM.

Vigilath-Siyuan Closed-Loop Reinforcement Learning: models GEO optimization as a self-learning loop of
"state (telemetry wide table/details) -> action (query x content combination) -> reward (hit/rank/citation delta)
-> policy update (UCB + gap prior)". The algorithm itself is in
`geo/services/vscl_rl.py`; this file only holds two persistence tables:

  - vscl_rl_arms    Arm statistics: one row per (topic, query, content direction, copy type),
                    cumulative pulls / reward sum; the policy's "memory"
  - vscl_rl_actions Action (episode) ledger: planned -> executed -> rewarded/stale,
                    records the context fingerprint and baseline snapshot at execution so the reward delta is trustworthy
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Column, DateTime, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint,
)

from geo.database import Base


class VsclRlArmORM(Base):
    """Multi-armed bandit arm statistics -- one row per (topic, query, direction, copywriting_type).

    arm_key = sha1("query|direction|type"), avoiding a unique index on a Text column;
    reward_sum stores the raw composite reward (may be negative), reward_norm_sum stores the reward normalized to [0,1]
    (the UCB confidence term uses normalized values), reward_sq_sum is reserved for variance/Bayesian extensions.
    """
    __tablename__ = "vscl_rl_arms"
    __table_args__ = (
        UniqueConstraint("topic_id", "arm_key", name="uq_vscl_arm"),
        Index("idx_vscl_arm_topic", "topic_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"), nullable=False)
    arm_key = Column(String(40), nullable=False)

    query = Column(Text, nullable=False)
    # Values align with the DIRECTION_HINTS / TYPE_HINTS keys in content_generator; empty string = unspecified
    direction = Column(String(64), nullable=False, default="")
    copywriting_type = Column(String(64), nullable=False, default="")

    pulls = Column(Integer, nullable=False, default=0)
    reward_sum = Column(Float, nullable=False, default=0.0)
    reward_norm_sum = Column(Float, nullable=False, default=0.0)
    reward_sq_sum = Column(Float, nullable=False, default=0.0)
    last_reward = Column(Float, nullable=True)
    last_pulled_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class VsclRlActionORM(Base):
    """Action (episode) ledger -- the full lifecycle of one "generate/place content for arm X".

    planned (selected by policy) -> executed (content generated/placed, recorded in doc_ids_json)
    -> rewarded (observation window closed, reward settled back to the arm) / stale (context fingerprint changed,
    reward voided and not credited to the arm -- snapshot consistency constraint) / cancelled (manually cancelled).

    baseline_json: baseline window stats of the query at arm selection time {runs, hits, top3, neg, cited},
    subtracted at settlement from the observation window using the same metric definition to get the delta reward.
    """
    __tablename__ = "vscl_rl_actions"
    __table_args__ = (
        Index("idx_vscl_action_topic_status", "topic_id", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"), nullable=False)
    arm_id = Column(Integer, ForeignKey("vscl_rl_arms.id", ondelete="CASCADE"), nullable=False)

    status = Column(String(16), nullable=False, default="planned")
    context_fingerprint = Column(String(40), nullable=False, default="")
    baseline_json = Column(Text, nullable=False, default="{}")
    doc_ids_json = Column(Text, nullable=False, default="[]")   # list[int] → topic_generated_docs.id

    planned_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    executed_at = Column(DateTime, nullable=True)

    reward = Column(Float, nullable=True)          # Raw composite reward
    reward_norm = Column(Float, nullable=True)     # Normalized [0,1]
    reward_detail_json = Column(Text, nullable=False, default="{}")  # Per-component details, for audit
    rewarded_at = Column(DateTime, nullable=True)
    note = Column(Text, nullable=True)
