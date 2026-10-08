"""AI telemetry topics — ORM + Pydantic schemas.

A user can create multiple topics (brand/competitor/industry); each topic = one search term + a set of queries × a set of AI engines.
Once enabled, telemetry-service runs it daily, and results land in the _runs / _responses tables of the same MySQL/SQLite DB.

v1 (hit tracking) — Topic.target/aliases + Response.hit/hit_excerpt/source_url + QueryHit matrix table
v1.1 (LLM diagnosis) — Response.competitors/citation_domains/answer_format + CellInsight table
v1.2 (weekly report)    — TopicBriefing table
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, SmallInteger, String, Text,
    UniqueConstraint, Index, text,
)

from geo.database import Base
from geo.services import topic_schedule as _sched


# ─────────────────────────── ORM ──────────────────────────────


class AiTelemetryTopicORM(Base):
    """AI telemetry topic configuration.

    v1 adds the `target` field (the search term being audited, e.g. "<law-firm-a>" (a law firm)) + `target_aliases_json` (alias list).
    `queries_json` is upgraded to support three shapes for backward compatibility:
      - list[str] (old version; all queries are treated as existing since topic creation)
      - list[{text, created_at}] (v1)
      - list[{text, created_at, cluster_id?}] (v2, cluster membership kept after picker-side clustering)
    `clusters_json`: cluster metadata from picker-side embeddings + K-Means, used to group batch-run results by cluster_id.
    """
    __tablename__ = "ai_telemetry_topics"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    name = Column(String, nullable=False)
    # Added in v1 — search term and aliases; backfilled from the sentiment account during migration
    target = Column(String, nullable=False, default="")
    target_aliases_json = Column(Text, nullable=False, default="[]")    # list[str]
    # Added in v2 (Phase C) — industry / business positioning. Previously only a temporary parameter of suggest-queries,
    # now persisted on the topic so it can be shown again when editing
    industry = Column(Text, nullable=False, default="")

    # Added in v2 (Phase C) — seed prompt list; frozen after review, append-only
    # list[{text, status, submitted_at, approved_at?, rejected_at?, reviewer_id?}]
    seed_prompts_json = Column(Text, nullable=False, default="[]")

    # Since v2 (Phase C) the query item schema adds status / submitted_at / approved_at:
    # list[str] | list[{text, created_at, cluster_id?}] | list[{text, status, submitted_at, ...}]
    # Since v3 (Phase D) query items also get `selected`: the ≤50 the user ticked from the expansion pool as monitoring targets
    queries_json = Column(Text, nullable=False, default="[]")
    clusters_json = Column(Text, nullable=False, default="[]")  # list[{cluster_id, label, size}]
    engines_json = Column(Text, nullable=False, default="[]")   # list[engine_id]
    # Scheduling switch — enabled is a **derived cache** of schedule_tier, not an independent switch:
    # tier='off' → enabled=False; tier in (daily, weekly) → enabled=True.
    # The single write point is PATCH /admin/topics/{id}/schedule (full PUT has a reverse-sync fallback);
    # downstream (list_enabled_topics / report center …) keeps reading enabled without knowing about tier.
    #
    # Since 2026-09-21 **new topics default to off**: previously the default was weekly, and as soon as step 3 of the wizard was saved,
    # the batch-run center's next hourly tick dispatched the whole matrix and pre-charged credits — the user only clicked "save",
    # and nothing told them it would cost money. Now new topics do not enter periodic batch runs; they run one round only when
    # "generate GEO strategy optimization plan" is clicked on the plan page (and only cells that never succeeded); those wanting continuous monitoring
    # switch to daily / weekly themselves on the "continuous monitoring" card. Existing topics' tiers are unchanged.
    enabled = Column(Boolean, nullable=False, default=False)
    # Batch-run tier: daily = one round per day / weekly = one round per week / off = not run (default since 2026-09-21)
    schedule_tier = Column(String(16), nullable=False, default="off", server_default="off")
    # Temporarily paused until this moment (naive UTC); resumes automatically when due, tier kept meanwhile so config is not lost
    paused_until = Column(DateTime, nullable=True)
    # Cycle anchor (topic-level override, migration c3d9b71e4a02): NULL = use the global env default
    #   cycle_anchor_weekday  0=Mon..6=Sun, only effective for weekly (daily opens a cycle every day)
    #   cycle_anchor_hour     0-23 local time, effective for both daily / weekly
    # Without these two columns all weekly topics were pinned to start on the same day (default Mon 02:05); changing the day meant editing env,
    # restarting telemetry, and changing it for everyone — the same rule as "switches must be visible in the back office".
    cycle_anchor_weekday = Column(SmallInteger, nullable=True)
    cycle_anchor_hour = Column(SmallInteger, nullable=True)

    # v3 (Phase D) review workflow: state machine of the whole application + profile snapshot
    # profile_json: 6-module brand profile form (basic identity / brand entity / core services / target users / brand story / content boundaries)
    profile_json = Column(Text, nullable=False, default="{}")
    submission_status = Column(String, nullable=False, default="draft")
    # ↑ draft / pending / approved / rejected — a different layer from seed_prompts_json[].status: whole application vs individual seed term
    submitted_at = Column(DateTime, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    rejected_at = Column(DateTime, nullable=True)
    reviewer_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # Topic log: each profile/seed/queries change appends {at, actor_id, actor_role, field, before, after}
    topic_changelog_json = Column(Text, nullable=False, default="[]")
    # Expansion log: records of seed term → LLM-expanded query candidate calls [{at, seed, model, expanded_count, raw_excerpt}]
    expansion_log_json = Column(Text, nullable=False, default="[]")

    last_run_at = Column(DateTime, nullable=True)
    last_run_status = Column(String, nullable=True)             # success / failed / running

    # v3.1 — AI auto-generation schedule (2026-05-17): one per topic.
    # cron scans hourly from 02:00 daily, triggering once for each topic matching auto_generate_time.
    auto_generate_enabled = Column(Boolean, nullable=False, default=False)
    auto_generate_time = Column(String(length=8), nullable=False, default="09:00")  # "HH:MM",24h, Asia/Shanghai
    auto_generate_count = Column(Integer, nullable=False, default=3)                 # Queries per run → number of drafts
    auto_generate_last_run_at = Column(DateTime, nullable=True)                       # UTC of the last automatic batch run
    # Auto-publish by publish_date switch (2026-06-09): on by default; admin can turn off subsequent publishing.
    # content_scheduler.publish_tick scans approved topics with auto_publish_enabled at 11:00 daily,
    # really publishing due (publish_date<=today) unpublished drafts in the execution plan to Mediumsly + marking other platforms.
    auto_publish_enabled = Column(Boolean, nullable=False, default=True)
    # Whether drafts are auto-approved (2026-09-10). Previously there was only the global env GEO_CONTENT_REVIEW_ENABLED,
    # with review off by default = approved directly — i.e. "auto-approval" was always the actual behavior, just invisible
    # and not configurable per topic. Promoted to a column: default True matches current behavior; turn it off for a topic to restore manual review.
    auto_approve_enabled = Column(Boolean, nullable=False, default=True)
    # Paid media (Ruanwenjiang / Meijieku) auto-ordering on the scheduled date (2026-09-10).
    # ★ Default **False**: this is the only switch that spends money on its own, and must be explicitly turned on by a person.
    #   When on, publish_tick orders due paid tasks directly instead of waiting for per-item confirmation in the publish center;
    #   insufficient balance / exceeding per-order or daily caps are always skipped (the same gate as manual ordering), never publishing on credit.
    auto_publish_paid_enabled = Column(Boolean, nullable=False, default=False)
    # At what time of day date-only rows (publish_date = YYYY-MM-DD) become due (2026-09-11).
    # Previously only the global env GEO_AUTO_PUBLISH_HOUR=11 existed, not user-changeable, and asymmetric with "generation time"
    # — auto_generate_time is configurable per topic, publish time was not.
    # ★ Row level takes precedence: rows whose publish_date is YYYY-MM-DDTHH:MM still publish on time; this column only covers
    #   rows without a specific time. Three-level priority = row-level time > this column > global env fallback.
    auto_publish_time = Column(String(length=8), nullable=False, default="11:00")  # "HH:MM"

    # Vigili·Siyuan loop switch (2026-07-26, P0): independent of auto_generate_enabled;
    # content_scheduler.siyuan_tick only scans topics with siyuan_enabled to run vscl_rl.run_tick →
    # pick an arm → trigger content generation. Off by default; canary on a single topic before widening.
    siyuan_enabled = Column(Boolean, nullable=False, default=False)
    siyuan_last_tick_at = Column(DateTime, nullable=True)   # UTC of the last Siyuan loop scheduling

    # Revision number — +1 on any field change (including admin edits / review state machine transitions).
    # Paired with topic_changelog_json: changelog entry N corresponds to the snapshot with version=N+1.
    # New topics start at version=1; every _append_changelog bumps it once.
    version = Column(Integer, nullable=False, default=1)

    # Extra prompt configured by admin for a single topic, appended to the end of the template when assembling batch-run prompts.
    # Invisible to regular users; only editable at /workbench/accounts/:userId/topic.
    prompt_extension = Column(Text, nullable=True)

    # Report center brand labels (2026-07) — benchmark labels users put on their own account's brand.
    # brand_tier: "" / new (new brand) / mid (mid-size brand) / leader (industry leader)
    # industry_class: "" / regular (regular industry) / special (special industry: medical, beauty, finance)
    # Report pages use them to match same-tier exposure reference ranges; "" = not set, no benchmark shown.
    brand_tier = Column(String(16), nullable=False, default="", server_default="")
    industry_class = Column(String(16), nullable=False, default="", server_default="")

    # Compliance audit center (2026-07, P0) — topic-level compliance profile, see chapter 4 of docs/06-发布运维/合规与资质/合规中心/合规审计中心-整体设计.md.
    # {ymyl_tier: 1|2|3|0, restrictions: [str],
    #  qualifications: [{type, ref, valid_until, evidence_ref}],
    #  disclaimers: [{label, required_texts: [str]}],
    #  required_qualification_types: [str]}
    compliance_profile_json = Column(Text, nullable=False, default="{}", server_default="{}")

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class AiTelemetryRunORM(Base):
    """Metadata of a single batch run (one row per topic per day)."""
    __tablename__ = "ai_telemetry_runs"

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"), nullable=False, index=True)
    operation_context_id = Column(
        Integer, ForeignKey("operation_contexts.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String, nullable=False, default="running")   # running / success / failed
    error = Column(Text, nullable=True)


class AiTelemetryResponseORM(Base):
    """Answer + citations + hit judgment + LLM-extracted fields for a single (engine × query).

    v1 adds: hit / hit_excerpt / source_url (hit judgment)
    v1.1 adds: competitors_json / citation_domains_json / answer_format (extracted asynchronously by LLM after the batch run)
    """
    __tablename__ = "ai_telemetry_responses"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("ai_telemetry_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"), nullable=False, index=True)
    engine = Column(String, nullable=False)
    query = Column(Text, nullable=False)
    answer = Column(Text, nullable=False, default="")
    citations_json = Column(Text, nullable=False, default="[]")
    video_url = Column(String, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # v1 hit judgment
    hit = Column(Boolean, nullable=False, default=False, index=True)
    hit_excerpt = Column(Text, nullable=True)
    source_url = Column(String, nullable=True)   # Public chat URL, e.g. yiyan.baidu.com/chat/xxx

    # v1.1 asynchronous LLM extraction
    competitors_json = Column(Text, nullable=True)         # [{name, count, snippet}]
    citation_domains_json = Column(Text, nullable=True)    # [domain, ...]
    answer_format = Column(String, nullable=True)          # listicle/single_recommendation/report/case_study/qa
    # v1.3 mention position (simplified search ranking): lead (beginning) / body (middle) / tail (end) / unknown
    # NULL when not hit (hit=False)
    mention_position = Column(String, nullable=True)
    # v1.4 (brand growth page) — the order in which the brand is mentioned in the AI answer, 1-based. NULL when not hit / not extracted.
    # Used to aggregate Top1/Top3/Top5 shares; mention_position is the paragraph position (lead/body/tail), different from brand_rank.
    brand_rank = Column(Integer, nullable=True)
    # v1.5 chain of thought (2026-09-16): the engine's "deep thinking/reasoning" part, stored separately from the body; hit judgment on the telemetry side
    # uses answer + reasoning (hit_excerpt starting with 「[思考过程] 」 ("[reasoning] ") = mentioned only in reasoning). NULL = not captured.
    reasoning = Column(Text, nullable=True)
    # v1.5 attribution — which worker and which pool account (engine_sessions.id) produced this response.
    # Persisted by telemetry-service /dispatch/result (returned by the worker with results); nullable: runner direct runs /
    # local-file login sessions / old workers lack it. No foreign key: deleting an account should not affect historical responses.
    worker_uid = Column(String, nullable=True)
    session_id = Column(Integer, nullable=True, index=True)
    # v1.6 engine-side collection parameters/status (2026-09-18, first used for kimi): JSON text of {thinking, status, search_triggered,
    # inline_refs, model, …}. Cross-period comparisons group by these parameters first, so samples with different parameters are not mixed.
    # Persisted by telemetry-service; old workers / non-kimi engines → NULL. Migration d8e4a2c71f95.
    meta_json = Column(Text, nullable=True)


class AiTelemetryQueryHitORM(Base):
    """Current state + first hit at the (query × engine) cell level.

    Storage panel of the v1 citation tracking matrix. Maintained when a Response is persisted:
      - find the (topic_id, query, engine) cell, total_runs+=1
      - if hit=True: total_hits+=1, fill first_hit_at if empty, same for first_hit_response_id
      - recompute status
    """
    __tablename__ = "ai_telemetry_query_hits"
    __table_args__ = (
        UniqueConstraint("topic_id", "query", "engine", name="uq_qh_cell"),
        Index("idx_qh_topic", "topic_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"), nullable=False)
    query = Column(Text, nullable=False)
    engine = Column(String, nullable=False)

    status = Column(String, nullable=False, default="pending")    # pending/running/done
    first_hit_at = Column(DateTime, nullable=True)
    first_hit_response_id = Column(Integer, nullable=True)
    last_checked_at = Column(DateTime, nullable=True)
    total_runs = Column(Integer, nullable=False, default=0)
    total_hits = Column(Integer, nullable=False, default=0)


class AiTelemetryCellInsightORM(Base):
    """v1.1 — Cell-level LLM diagnosis + optimization suggestions. Triggered on demand, cached per window.

    The primary key includes window_end: the same cell can have several windows coexisting (historical lookback). LLM calls are expensive;
    the lifecycle is managed by telemetry-service's /cell-insight endpoint:
      - if the cache already has the same window_end and evidence_response_ids are unchanged → read directly
      - otherwise call the LLM to generate a new row (prompt_version carries a version number for A/B)
    """
    __tablename__ = "ai_telemetry_cell_insights"
    __table_args__ = (
        UniqueConstraint("topic_id", "query", "engine", "window_end", "prompt_version",
                         name="uq_ci_cell_window"),
        Index("idx_ci_topic", "topic_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"), nullable=False)
    query = Column(Text, nullable=False)
    engine = Column(String, nullable=False)

    window_start = Column(DateTime, nullable=False)
    window_end = Column(DateTime, nullable=False)

    verdict = Column(String, nullable=False)              # hit_stable/hit_unstable/near_miss/no_signal/negative_mention
    summary = Column(Text, nullable=False, default="")
    competitors_top3_json = Column(Text, nullable=False, default="[]")     # [{name, count, snippet}]
    recommendations_json = Column(Text, nullable=False, default="[]")      # [{priority, title, action, why}]
    evidence_response_ids_json = Column(Text, nullable=False, default="[]")

    llm_model = Column(String, nullable=False, default="")
    prompt_version = Column(String, nullable=False, default="cell_v1")
    generated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    feedback = Column(String, nullable=True)              # helpful/not_helpful/wrong


class AiTelemetryTopicBriefingORM(Base):
    """v1.2 — Topic-level weekly report. The scheduler runs it every Monday at 09:00 Beijing time."""
    __tablename__ = "ai_telemetry_topic_briefings"
    __table_args__ = (
        UniqueConstraint("topic_id", "period_end", "prompt_version", name="uq_tb_period"),
        Index("idx_tb_topic", "topic_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"), nullable=False)
    operation_context_id = Column(
        Integer, ForeignKey("operation_contexts.id", ondelete="RESTRICT"), nullable=True,
        index=True,
    )
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    # Granularity: weekly (weekly report, historical default) | daily (flywheel P1-A daily report)
    period_type = Column(String, nullable=False, default="weekly", server_default="weekly")

    body_md = Column(Text, nullable=False, default="")
    kpi_snapshot_json = Column(Text, nullable=False, default="{}")
    top_actions_json = Column(Text, nullable=False, default="[]")

    delivered_email_at = Column(DateTime, nullable=True)
    feedback_score = Column(Integer, nullable=True)       # 1-5 stars

    llm_model = Column(String, nullable=False, default="")
    prompt_version = Column(String, nullable=False, default="briefing_v1")
    generated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AiTelemetryTopicSourceStrategyORM(Base):
    """Flywheel P1-D — source strategy: why cited + media placement recommendations.

    At most one per topic (unique topic_id); regeneration overwrites in place (same pattern as Solution):
      1. aggregate citations across all of the topic's responses (external source ranking / type distribution / per-engine differences /
         answer_format distribution) → source_stats_json (hard data, no LLM)
      2. LLM analyzes "why the model cites these articles" + recommends placement media → analysis_json /
         recommendations_json
    recommendations also feed: the citation-friendly writing block in content generation (P1-E), and the task center (P2).

    State machine: idle → generating → ready / failed.
    """
    __tablename__ = "ai_telemetry_topic_source_strategy"
    __table_args__ = (
        UniqueConstraint("topic_id", name="uq_topic_source_strategy"),
        Index("idx_topic_source_strategy_topic", "topic_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"), nullable=False)
    operation_context_id = Column(
        Integer, ForeignKey("operation_contexts.id", ondelete="RESTRICT"), nullable=True,
        index=True,
    )
    status = Column(String(length=16), nullable=False, default="idle")  # idle / generating / ready / failed
    # Hard-data aggregate snapshot: top external sources / type distribution / per-engine top domains and answer_format distribution / missed terms
    source_stats_json = Column(Text, nullable=False, default="{}")
    # LLM: citation pattern analysis (why these sources are cited / relation between model results and sources / per-engine differences)
    analysis_json = Column(Text, nullable=False, default="{}")
    # LLM: recommended placement media [{platform, priority, reason, content_type}]
    recommendations_json = Column(Text, nullable=False, default="[]")
    llm_model = Column(String(length=64), nullable=False, default="")
    prompt_version = Column(String(length=32), nullable=False, default="source_strategy_v1")
    error = Column(Text, nullable=True)
    generated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class GeoRetestProbeORM(Base):
    """Flywheel P1-C — targeted post-placement retest probe.

    Registered on successful publish (retest_service.schedule_for_doc): records the doc's associated
    query×engine, placement URLs and pre-publish baseline metrics; after GEO_RETEST_DELAY_DAYS the
    telemetry-service scheduling loop picks it up → inserts dispatch_tasks (priority=0 for first
    claim, automatically counted against engine daily caps) / API engines run directly; after 24h the post metrics are computed with the same definition,
    result_json = {before, after, delta}.

    State machine: scheduled → enqueued → done (or cancelled).
    """
    __tablename__ = "geo_retest_probes"
    __table_args__ = (
        Index("idx_retest_probe_topic", "topic_id"),
        Index("idx_retest_probe_status", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"), nullable=False)
    doc_id = Column(Integer, ForeignKey("topic_generated_docs.id", ondelete="CASCADE"), nullable=False)
    queries_json = Column(Text, nullable=False, default="[]")    # list[str]
    engines_json = Column(Text, nullable=False, default="[]")    # list[str]
    doc_urls_json = Column(Text, nullable=False, default="[]")   # Placement URLs (for citation attribution)
    baseline_json = Column(Text, nullable=False, default="{}")   # Pre-publish metric snapshot
    scheduled_for = Column(DateTime, nullable=False)             # Due time (UTC)
    enqueued_at = Column(DateTime, nullable=True)                # Actual enqueue time
    status = Column(String(16), nullable=False, default="scheduled")  # scheduled/enqueued/done/cancelled
    result_json = Column(Text, nullable=False, default="{}")     # {before, after, delta}
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class GeoActionItemORM(Base):
    """Flywheel P2 — optimization task center: scattered suggestions consolidated into a unified action item queue.

    Producers (side-channel writes, idempotently deduped by topic_id+source+source_key):
      - daily_brief: daily report top_actions (telemetry-service briefings.py)
      - retest: retest delta<0 (telemetry-service retests.py)
      - cell_insight: cell insight suggestions (telemetry-service insights.py)
      - manual: entered manually by ops
    Consumer: geo/api/action_items.py — list / dismiss / convert
    (convert=add_plan_item calls P1-B insert-items, =retest calls P1-C schedule_for_doc).
    Compliance tickets stay in the original system and are not migrated to the task center.
    """
    __tablename__ = "geo_action_items"
    __table_args__ = (
        UniqueConstraint("topic_id", "source", "source_key", name="uq_action_item_src"),
        Index("idx_action_item_topic_status", "topic_id", "status"),
    )

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"), nullable=False)
    source = Column(String(24), nullable=False)              # daily_brief/retest/cell_insight/source_strategy/manual
    source_key = Column(String(128), nullable=False)         # Dedup key (e.g. "2026-07-19:0" / "probe:12")
    source_ref_json = Column(Text, nullable=False, default="{}")
    priority = Column(String(4), nullable=False, default="P1")   # P0/P1/P2
    title = Column(String(200), nullable=False, default="")
    body_md = Column(Text, nullable=False, default="")
    suggested_action = Column(String(24), nullable=False, default="other")  # add_plan_item/retest/fix_content/other
    status = Column(String(16), nullable=False, default="open")  # open/converted/dismissed/done
    linked_plan_item_id = Column(String(64), nullable=True)
    linked_doc_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)


RESPONSE_DETAIL_SCHEMA_VERSION = 1   # +1 when the parsing definition changes


class AiTelemetryResponseDetailORM(Base):
    """Response-level flattened detail table (parsing layer) — one row = one response, with JSON fully split into real columns.

    The middle layer of a three-layer structure: responses (raw) → this table (parsed details) → report_daily (daily rollup).
    Any data can be queried directly with SQL; data fixes are direct column UPDATEs.
    is_negative / cites_owned are frozen when parsed and persisted (the last 3 days are refreshed by the nightly job);
    competitors beyond 5 only go into competitor_count / comp_names_text, likewise citations beyond 5 (*_text holds the full fallback).
    competitor_count strictly matches the rollup definition (length of _comp_names).
    """
    __tablename__ = "ai_telemetry_response_detail"
    __table_args__ = (
        UniqueConstraint("response_id", name="uq_response_detail_resp"),
        Index("idx_response_detail_topic_day", "topic_id", "day"),
        Index("idx_response_detail_topic_engine", "topic_id", "engine"),
    )

    id = Column(Integer, primary_key=True, index=True)
    response_id = Column(Integer, ForeignKey("ai_telemetry_responses.id", ondelete="CASCADE"), nullable=False)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"), nullable=False)
    run_id = Column(Integer, nullable=True)

    # ── Dimensions ──
    day = Column(String(10), nullable=False)          # Beijing date "2026-07-04"
    ts_bj = Column(DateTime, nullable=False)          # Beijing time
    engine = Column(String(32), nullable=False)
    query = Column(Text, nullable=False)
    scene_type = Column(String(16), nullable=False, default="unknown")

    # ── Judgments ──
    is_valid = Column(Boolean, nullable=False, default=True)    # No error
    error_text = Column(Text, nullable=True)
    hit = Column(Boolean, nullable=False, default=False)
    brand_rank = Column(Integer, nullable=True)
    is_top1 = Column(Boolean, nullable=False, default=False)
    is_top3 = Column(Boolean, nullable=False, default=False)
    mention_position = Column(String(8), nullable=True)         # lead/body/tail/unknown
    is_negative = Column(Boolean, nullable=False, default=False)
    sentiment = Column(String(8), nullable=False, default="unknown")  # positive/negative/unknown
    answer_format = Column(String(32), nullable=True)

    # ── Competitors ──
    competitor_count = Column(Integer, nullable=False, default=0)
    comp_names_text = Column(Text, nullable=False, default="")  # Joined with " | "
    comp1_name = Column(String(128), nullable=True)
    comp1_count = Column(Integer, nullable=True)
    comp2_name = Column(String(128), nullable=True)
    comp2_count = Column(Integer, nullable=True)
    comp3_name = Column(String(128), nullable=True)
    comp3_count = Column(Integer, nullable=True)
    comp4_name = Column(String(128), nullable=True)
    comp4_count = Column(Integer, nullable=True)
    comp5_name = Column(String(128), nullable=True)
    comp5_count = Column(Integer, nullable=True)

    # ── Citations ──
    citation_count = Column(Integer, nullable=False, default=0)
    cites_owned = Column(Boolean, nullable=False, default=False)
    owned_cite_count = Column(Integer, nullable=False, default=0)
    cite_domains_text = Column(Text, nullable=False, default="")
    cite_urls_text = Column(Text, nullable=False, default="")
    cite1_url = Column(Text, nullable=True)
    cite1_type = Column(String(32), nullable=True)
    cite1_owned = Column(Boolean, nullable=True)
    cite2_url = Column(Text, nullable=True)
    cite2_type = Column(String(32), nullable=True)
    cite2_owned = Column(Boolean, nullable=True)
    cite3_url = Column(Text, nullable=True)
    cite3_type = Column(String(32), nullable=True)
    cite3_owned = Column(Boolean, nullable=True)
    cite4_url = Column(Text, nullable=True)
    cite4_type = Column(String(32), nullable=True)
    cite4_owned = Column(Boolean, nullable=True)
    cite5_url = Column(Text, nullable=True)
    cite5_type = Column(String(32), nullable=True)
    cite5_owned = Column(Boolean, nullable=True)

    # ── Other ──
    source_url = Column(String, nullable=True)
    hit_excerpt = Column(Text, nullable=True)
    answer_chars = Column(Integer, nullable=False, default=0)   # The raw text stays in responses; only the length is stored

    schema_version = Column(Integer, nullable=False, default=RESPONSE_DETAIL_SCHEMA_VERSION)
    source = Column(String(16), nullable=False, default="job")  # job / demo / backfill
    generated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


REPORT_DAILY_SCHEMA_VERSION = 2   # +1 when the report definition changes; readers only accept the current version
# v2 (2026-07-20): added cat_* category-term count columns — exposure/TOP1/TOP3 globally exclude
# terms with scene_type ∈ {brand, compare} (brand terms / competitor-comparison terms), see EXPOSURE_ONLY_SCENES in reports

# Topics whose metric statistics exclude brand terms (per-topic switch, global definition unchanged):
# for real accounts with a high share of brand terms in the vocabulary, "ask about the brand, answer with the brand" inflates exposure (example cleaning brand terms 57/103,
# exposure 63% vs 18% under the category-term definition). Once listed: six metrics / wide table / competitor comparison count only category terms
# (term text not containing brand name/aliases); rank distribution and response details are unaffected, brand terms shown as usual.
METRIC_EXCLUDE_BRAND_TOPIC_IDS: frozenset[int] = frozenset({9})


# ── Admin-only settings in profile data ──────────────────────────────────────────────
# Stored under the reserved key "_admin" of topic.profile_json (not part of the BrandProfile form; customers can neither see nor change it).
# Why profile_json instead of a column: these settings are used only by ops and read/written on the profile page; adding a column needs a migration on the shared appdb
# (test host does not run them automatically, prod migration files often lag), not worth it. Saving the profile must merge to keep it —
# the client-submitted BrandProfile lacks this section, and a full overwrite would wipe it.
PROFILE_ADMIN_KEY = "_admin"
# Response denominator **setting**: the topic's target for "how many question × platform combinations one period should cover".
# Semantics are **pinned** (since 2026-08-31; before that it was a floor, max(actual, value)) —
# denom = the value, regardless of how much this period actually covered, so columns and periods share one scale;
# the cost is that when actual volume exceeds the value the numerator exceeds it, output is clamped to 100% and a warning logged.
# Affects overall exposure / TOP1 / TOP3 (all three on one scale) / article citation rate; neutral-positive rate (denominator = hits) and
# share of voice (denominator = total mentions) are unaffected. Only effective for weekly / monthly reports; daily reports always use the real denominator.
# Full definition in docs/03-系统设计/报告与指标/报告口径-应答分母托底.md.
FIXED_RESPONSE_KEY = "fixed_response_count"
# Share-of-voice numerator counts only category-term hits: brand-term questions name the brand themselves ("how is XX"), so the AI must mention it and it must hit;
# counting them in the numerator inflates share of voice — in the example tax-finance period of 06-29 all 109 hits came from brand terms, and the 24% share of voice
# was not earned by category terms at all. Exposure v2 already excludes brand terms; this switch aligns share of voice with the same definition.
MENTION_CAT_KEY = "mention_cat_scope"


def _profile_dict(topic) -> dict:
    import json as _json
    try:
        d = _json.loads(getattr(topic, "profile_json", "") or "{}")
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def profile_admin_settings(topic) -> dict:
    """The admin section of the profile data (empty dict by default)."""
    seg = _profile_dict(topic).get(PROFILE_ADMIN_KEY)
    return seg if isinstance(seg, dict) else {}


def fixed_response_count(topic) -> Optional[int]:
    """The topic's response denominator floor; unconfigured / not a positive integer → None (use actual response counts throughout)."""
    raw = profile_admin_settings(topic).get(FIXED_RESPONSE_KEY)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def mention_cat_scope(topic) -> bool:
    """Whether the topic's share-of-voice numerator counts only category-term hits (excluding brand terms)."""
    return bool(profile_admin_settings(topic).get(MENTION_CAT_KEY))


def merge_profile_json(topic, profile_dump: dict) -> str:
    """Client-submitted profile + the existing admin section kept → the new profile_json string.

    ★ Every place that writes profile_json must go through here; a direct json.dumps(payload.model_dump())
      would wipe the whole _admin section (config silently vanishes and the report definition quietly reverts to the actual denominator).
    """
    import json as _json
    out = dict(profile_dump)
    admin_seg = profile_admin_settings(topic)
    if admin_seg:
        out[PROFILE_ADMIN_KEY] = admin_seg
    return _json.dumps(out, ensure_ascii=False)


def deleted_query_texts(topic) -> frozenset[str]:
    """Prompt texts of the topic that are **soft-deleted**. The single repo-wide entry point — do not parse queries_json yourself elsewhere.

    Definition in docs/03-系统设计/报告与指标/提示词软删除.md. Missing `deleted_at` = not deleted (versus the truthy defaults of
    selected/status: when those two are missing it means "participating", and when this one is missing it also means "participating").

    ★ Read points that must call it (soft-deleted terms must drop out):
        batch-run term loading / report six-metric row loading / publishing term selection and plan scheduling / retest probe registration / strategy plan term list /
        term × engine matrix / the agent's placement and monitoring sets / term-selection quota counting
    ★ Read points that **must not** call it (soft-deleted terms must still appear):
        ai_telemetry_reports._scene_of_map / _metric_excl_queries — they look up scene_type by term text;
            filtering would make historical brand-term responses count as category terms, inflating exposure (see the QueryItem.deleted_at comment)
        services/response_detail._scene_by_query — same as above, and the value is frozen into detail table columns, irreversible if wrong
        TopicOut.from_orm_row — must return all elements + the parallel query_deleted array;
            the frontend's "include deleted" toggle and restore button depend entirely on it
        api/ai_telemetry._queries_with_meta — must pass soft-deleted items through as-is, or a single save physically erases them
        historical tracing (doc.source_query_text / plan_link / retest_service._doc_queries /
            describe_generation_inputs / publishing ledger) — a published draft was written for that term,
            and hiding it would blank the "creation relations" section; the right approach is to show it as usual + a "deleted" badge
        storage layer (responses / response_detail / query_hits) — never deleted, never filtered at the storage layer
    """
    import json as _json
    try:
        raw = _json.loads(getattr(topic, "queries_json", "") or "[]")
    except Exception:  # noqa: BLE001
        return frozenset()
    return frozenset(
        q["text"] for q in raw
        if isinstance(q, dict) and q.get("text") and q.get("deleted_at")
    )


def active_query_texts(topic) -> list[str]:
    """Prompt texts the topic **will actually run** (keeping the original order in queries_json). See deleted_query_texts.

    Two exclusions: soft-deleted terms (`deleted_at`) and terms the user explicitly unticked (`selected` explicitly false).

    ★ This is a **deliberate duplicate implementation** of telemetry's `app/storage.py:parse_topic` — telemetry
      is deployed independently and cannot import backend; change one side and you must change the other, or the two sides compute different numbers for
      "how many terms this round": pre-charging uses this one, actual running uses that one, and the money will not match.

    ★ The selected criterion is "explicitly false", not "not true": the vast majority of terms in the DB lack
      this field entirely (written via PUT /topics), and filtering on "not true" would zero out running topics overnight.
    """
    import json as _json
    try:
        raw = _json.loads(getattr(topic, "queries_json", "") or "[]")
    except Exception:  # noqa: BLE001
        return []
    out: list[str] = []
    for q in raw:
        if isinstance(q, dict):
            if q.get("text") and not q.get("deleted_at") and q.get("selected") is not False:
                out.append(q["text"])
        elif isinstance(q, str) and q:
            out.append(q)                      # The legacy str form has no soft-delete concept
    return out


# ── Product list in the brand profile (profile_json.products, see BrandProduct) ────────────────────
def profile_products(profile: dict | None) -> list[dict]:
    """Product rows in the profile_json dict (only dicts with a name are kept), order preserved."""
    if not isinstance(profile, dict):
        return []
    raw = profile.get("products")
    if not isinstance(raw, list):
        return []
    return [p for p in raw if isinstance(p, dict) and str(p.get("name") or "").strip()]


def _product_own_terms(p: dict) -> list[str]:
    """A single product's terms: name + aliases (aliases may be an unsplit string), dropping those equal to its own category or shorter than 2."""
    cat = str(p.get("category") or "").strip().casefold()
    cands = [str(p.get("name") or "")]
    al = p.get("aliases")
    if isinstance(al, list):
        cands += [str(a or "") for a in al]
    elif isinstance(al, str):
        cands += _PRODUCT_ALIAS_SEP.split(al)
    out: list[str] = []
    for c in cands:
        c = c.strip()
        if len(c) >= 2 and c.casefold() != cat:
            out.append(c)
    return out


def profile_product_terms(profile: dict | None, *, industry: str = "") -> list[str]:
    """Product names + product aliases → brand terms (length ≥2).

    Drop those equal to the product's category or the topic's industry: when a user fills 「手柄」 ("gamepad") as both a product name and a category,
    once in the brand terms, the bidirectional containment matching of reports._is_brand_alias would erase every competitor containing 「手柄」 as our own alias.
    """
    ind = (industry or "").strip().casefold()
    out: list[str] = []
    seen: set[str] = set()
    for p in profile_products(profile):
        for c in _product_own_terms(p):
            k = c.casefold()
            if k in seen or (ind and k == ind):
                continue
            seen.add(k)
            out.append(c)
    return out


def featured_products(products: list[dict]) -> list[dict]:
    """Featured products first (keeping relative order), followed by the other products."""
    ps = [p for p in (products or []) if isinstance(p, dict)]
    return [p for p in ps if p.get("is_featured")] + [p for p in ps if not p.get("is_featured")]


def find_product(products: list[dict], seed: str) -> Optional[dict]:
    """Is the seed one of this brand's products: bidirectional containment (casefold), exact > longest containment.

    「<brand-e>」 hits the product 「<brand-e>手柄」 (brand + "gamepad"); 「<brand-e>手柄值得买吗」 ("is ... worth buying") also hits. Both names and aliases count,
    with the same term definition as _product_own_terms (a name equal to the category is not a product identifier).
    """
    s = (seed or "").strip().casefold()
    if len(s) < 2:
        return None
    best: Optional[dict] = None
    best_len = 0
    for p in products or []:
        if not isinstance(p, dict):
            continue
        for n in _product_own_terms(p):
            n = n.casefold()
            if n == s:
                return p
            # The product term is contained in the seed (「<brand-e>手柄值得买吗」); or the seed is most of the product term
            # (「<brand-e>」→「<brand-e>手柄」). A seed that is too short / only a small part of the product term (「手柄」 ("gamepad")) does not count —
            # otherwise pure category seeds would be dragged into product mode.
            contained = n in s or (len(s) >= 3 and s in n and len(s) * 2 >= len(n))
            if contained and len(n) > best_len:
                best, best_len = p, len(n)
    return best


def metric_brand_terms(topic) -> list[str]:
    """Brand terms used for metric term exclusion: target split by / + aliases + **product names / product aliases**; only length ≥2 participates in matching.

    Since 2026-09-22 names from profile_json.products are merged in (see profile_product_terms): an AI answer mentioning only
    「<brand-e>手柄」 but not 「<brand-f>」 (the brand) still counts as mentioning the brand; terms like 「<brand-e>手柄怎么样」 ("how is ...") are brand terms too.

    ★ The copy that sets hit in batch runs (V-networks `worker-center/app/storage.py:parse_target`) is a
      **deliberate duplicate implementation** of this function — that side is deployed independently and cannot import backend; change one side and you must change the other, otherwise
      the batch run judges a hit while the report treats the same entry as a competitor.
    """
    import json as _json
    names = {p.strip() for p in (getattr(topic, "target", "") or "").split("/") if len(p.strip()) >= 2}
    try:
        names |= {str(a).strip() for a in _json.loads(getattr(topic, "target_aliases_json", "") or "[]")
                  if len(str(a).strip()) >= 2}
    except Exception:  # noqa: BLE001
        pass
    names |= set(profile_product_terms(_profile_dict(topic), industry=getattr(topic, "industry", "") or ""))
    return sorted(names)


class AiTelemetryReportDailyORM(Base):
    """Report center daily wide table — one row per (topic, Beijing date, engine).

    engine='__all__' is the daily total row, plus one row per engine (the engine set varies, not hard-coded as column names).
    Count columns are the authoritative source (weekly/monthly reports sum counts per period and recompute ratios, byte-identical to per-row aggregation);
    percentage columns are redundant values computed at write time, for human reading/checking/synthetic data.
    Negative window judgments are frozen at persistence — per the established report definition: reports for historical windows use that period's judgment,
    not rewritten by future judgments.
    """
    __tablename__ = "ai_telemetry_report_daily"
    __table_args__ = (
        UniqueConstraint("topic_id", "day", "engine", name="uq_report_daily_cell"),
        Index("idx_report_daily_topic", "topic_id", "day"),
    )

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"), nullable=False)
    day = Column(String(10), nullable=False)          # Beijing date "2026-07-04"
    engine = Column(String(32), nullable=False, default="__all__")

    # ── Count columns (authoritative source) ──
    valid_count = Column(Integer, nullable=False, default=0)   # Error-free responses (= the report's "responses")
    hit_count = Column(Integer, nullable=False, default=0)     # Hits (exposure numerator / voice numerator)
    top1_count = Column(Integer, nullable=False, default=0)
    top3_count = Column(Integer, nullable=False, default=0)
    neg_hit_count = Column(Integer, nullable=False, default=0) # Hits within negative windows (neutral-positive rate)
    cited_count = Column(Integer, nullable=False, default=0)   # Responses citing owned articles
    comp_mention_count = Column(Integer, nullable=False, default=0)  # Total competitor appearances (voice denominator)
    # cat_* category-term definition (excluding brand terms / competitor-comparison terms), only for the exposure/TOP1/TOP3 metrics (schema v2)
    cat_valid_count = Column(Integer, nullable=False, default=0)
    cat_hit_count = Column(Integer, nullable=False, default=0)
    cat_top1_count = Column(Integer, nullable=False, default=0)
    cat_top3_count = Column(Integer, nullable=False, default=0)

    # ── Redundant percentage columns ──
    exposure_pct = Column(Float, nullable=False, default=0.0)
    top1_pct = Column(Float, nullable=False, default=0.0)
    top3_pct = Column(Float, nullable=False, default=0.0)
    accuracy_pct = Column(Float, nullable=False, default=100.0)
    citation_pct = Column(Float, nullable=False, default=0.0)
    mention_pct = Column(Float, nullable=False, default=0.0)

    schema_version = Column(Integer, nullable=False, default=REPORT_DAILY_SCHEMA_VERSION)
    source = Column(String(16), nullable=False, default="job")   # job / demo / backfill
    generated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AiTelemetryTopicExecutionPlanORM(Base):
    """v3 (Phase D) — execution plan snapshot generated on approval.

    Contains: immutable copies of profile + prompts + topic/expansion logs (kept as generated even if the topic is later edited)
    + the run_id of that "run on approval"; run progress is aggregated live from the _query_hits table.
    """
    __tablename__ = "ai_telemetry_topic_execution_plans"
    __table_args__ = (Index("idx_tep_topic", "topic_id"),)

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"), nullable=False)
    generated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    generated_by_reviewer_id = Column(Integer, nullable=True)

    # Overall project status snapshot: profile + prompt count + engine list
    overview_json = Column(Text, nullable=False, default="{}")
    # Topic log snapshot (copy of topic_changelog_json at generation time)
    topic_changelog_snapshot_json = Column(Text, nullable=False, default="[]")
    # Expansion log snapshot (copy of expansion_log_json at generation time)
    expansion_log_snapshot_json = Column(Text, nullable=False, default="[]")
    # Prompt list snapshot (texts of approved and selected queries)
    monitored_queries_snapshot_json = Column(Text, nullable=False, default="[]")

    run_id = Column(Integer, ForeignKey("ai_telemetry_runs.id", ondelete="SET NULL"), nullable=True)
    status = Column(String, nullable=False, default="draft")    # draft / confirmed / failed
    error = Column(Text, nullable=True)
    # Editor: publishing plan rows (list of the persisted field subset of PublishPlanItem)
    publishing_plan_json = Column(Text, nullable=False, default="[]")
    confirmed_at = Column(DateTime, nullable=True)
    confirmed_by_id = Column(Integer, nullable=True)
    last_edited_at = Column(DateTime, nullable=True)


class ContentTemplateORM(Base):
    """Content templates — attach a prompt template to each publishing row in the execution plan.

    scope:
      - system: global presets usable by all ops; only super admins may edit
      - tenant: tenant (user)-level templates, visible and editable by that user
      - topic: topic-level templates attached to a topic, only visible in that topic's editor

    After locked=True, the prompt_template / kind fields become read-only (API validation + greyed out in the frontend).
    Locking does not stop publishing plans from continuing to reference the template; each LLM call renders with the "current prompt".
    """
    __tablename__ = "content_templates"
    __table_args__ = (
        Index("idx_content_template_scope", "scope"),
        Index("idx_content_template_tenant", "tenant_id"),
        Index("idx_content_template_topic", "topic_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    scope = Column(String(length=8), nullable=False, default="tenant")   # system / tenant / topic
    tenant_id = Column(Integer, nullable=True)
    topic_id = Column(Integer, nullable=True)
    name = Column(String(length=64), nullable=False, default="")
    kind = Column(String(length=16), nullable=False, default="长文")      # 长文/短文/FAQ/案例/测评/对比/教程/问答 (long-form/short-form/FAQ/case study/review/comparison/tutorial/Q&A)
    prompt_template = Column(Text, nullable=False, default="")
    target_platforms_json = Column(Text, nullable=False, default="[]")
    length_min = Column(Integer, nullable=False, default=800)
    length_max = Column(Integer, nullable=False, default=1500)
    locked = Column(Boolean, nullable=False, default=False)
    locked_at = Column(DateTime, nullable=True)
    locked_by_id = Column(Integer, nullable=True)
    created_by_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class AiTelemetryTopicSolutionORM(Base):
    """v3.3 (2026-05-21) — GEO brand growth strategy plan.

    At most one per topic (unique topic_id). Admin triggers generation in the workbench for an approved topic:
      1. run geo_checker.run_geo_check to get the 25-category diagnosis → diagnosis_json
      2. cluster the 25 categories by theme into 5 major weakness clusters → fed to the LLM (DeepSeek)
      3. the LLM writes the plan copy (diagnosis narrative / seven-step customization / 6-layer keywords / vision) → narrative_json / keywords_json
      4. brand_snapshot_json: an immutable snapshot of BrandProfile at generation time (for comparison on later regeneration)

    State machine: idle → generating → ready / failed.
    Regeneration overwrites in place (unique topic_id), replacing the previous content.
    """
    __tablename__ = "ai_telemetry_topic_solutions"
    __table_args__ = (
        UniqueConstraint("topic_id", name="uq_topic_solution"),
        Index("idx_topic_solution_topic", "topic_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"), nullable=False)
    operation_context_id = Column(
        Integer, ForeignKey("operation_contexts.id", ondelete="RESTRICT"), nullable=True,
        index=True,
    )
    status = Column(String(length=16), nullable=False, default="idle")  # idle / generating / ready / failed
    website_url = Column(String(length=512), nullable=False, default="")
    # 25-category scan results + 5 major weakness clusters + score / grade
    diagnosis_json = Column(Text, nullable=False, default="{}")
    # Copy from the LLM: diagnosis narrative / seven-step customization / execution flow / vision
    narrative_json = Column(Text, nullable=False, default="{}")
    # 6-layer keyword system from the LLM
    keywords_json = Column(Text, nullable=False, default="{}")
    # BrandProfile snapshot at generation time
    brand_snapshot_json = Column(Text, nullable=False, default="{}")
    # Monitoring query snapshot at generation time — {"queries":[{text,cluster_id,seed}], "clusters":[{cluster_id,label}]}
    # Taken directly from topic.queries_json (selected=True) + topic.clusters_json, no LLM
    queries_snapshot_json = Column(Text, nullable=False, default="{}")
    llm_model = Column(String(length=64), nullable=False, default="")
    error = Column(Text, nullable=True)
    generated_by_admin_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class TopicGeneratedDocORM(Base):
    """v3 (Phase D) — content copy drafts generated by the LLM from the profile + approved prompts.

    Lifecycle: draft → pending_review → approved/rejected → published (after approved, choose publishing platforms / media)
    publish_targets_json does not actually call external platform OpenAPIs; it only records "marked as published where".
    """
    __tablename__ = "topic_generated_docs"
    __table_args__ = (
        Index("idx_tgd_topic", "topic_id"),
        Index("idx_tgd_status", "status"),
        Index("idx_tgd_plan_item", "plan_item_id"),
        Index("idx_gen_doc_vscl_action", "vscl_action_id"),
        Index("idx_tgd_deleted", "deleted_at"),
        # The single entry for the provenance page's reverse lookup by content number; must be unique — the number contains doc.id, naturally unique
        Index("idx_tgd_trace_no", "gen_trace_no", unique=True),
    )

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"), nullable=False)
    execution_plan_id = Column(Integer, ForeignKey("ai_telemetry_topic_execution_plans.id", ondelete="SET NULL"), nullable=True)
    # Link to a row in the execution plan editor (publishing_plan_items[].id, a uuid string)
    plan_item_id = Column(String(length=36), nullable=True)
    # Template + platform used at generation time (snapshot values of the plan_item at that time)
    template_id = Column(Integer, ForeignKey("content_templates.id", ondelete="SET NULL"), nullable=True)
    platform = Column(String(length=16), nullable=True)
    # 2026-05-28 — multi-variant generation: the same query produces several drafts of different styles per (creation_direction, copywriting_type) combo;
    # these two fields identify the combo. Left empty for legacy docs.
    creation_direction = Column(String(length=64), nullable=True)
    copywriting_type = Column(String(length=64), nullable=True)
    # Vigili·Siyuan (2026-07-26, P0): which VSCL-RL action triggered generation of the draft.
    # Written directly by siyuan_tick at generation time, so reward settlement/citation attribution trace stably to a specific action,
    # replacing reverse reconciliation by (query, direction, type, time) coordinates alone. Empty for legacy / user-supplied drafts.
    vscl_action_id = Column(Integer, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    # 2026-08-10 — regeneration is an **in-place overwrite** (doc id / created_at unchanged); looking only at created_at,
    # "a draft rewritten today" would sink to the day it was originally created, with no visible sign of change in the list.
    # onupdate refreshes it on any UPDATE, and article management uses it to support sorting by update time.
    updated_at = Column(DateTime, nullable=True, default=datetime.utcnow,
                        onupdate=datetime.utcnow)
    # 2026-08-13 — soft delete. **No hard deletes**: drafts carry placement tasks, retest probes and citation attribution,
    # and physically deleting them would erase the evidence chain, never reconcilable afterwards. Delete = set a flag + never shown in the frontend,
    # restorable when needed. Data loading always adds deleted_at.is_(None) (see §"soft-delete filter points").
    deleted_at = Column(DateTime, nullable=True)
    deleted_by_id = Column(Integer, nullable=True)
    source_query_text = Column(Text, nullable=False, default="")
    title = Column(String, nullable=False, default="")
    body_markdown = Column(Text, nullable=False, default="")
    summary = Column(Text, nullable=False, default="")          # Summary within 200 characters — for cards
    llm_model = Column(String, nullable=False, default="")
    generation_error = Column(Text, nullable=True)
    # 2026-08-04 — requeue on generation failure (services/gen_retry.py). Failed draft rows are the queue items:
    # gen_attempts is the number of automatic retries so far (reset to zero on success), gen_next_retry_at the next retry time
    # (None = not queued: success / terminal error / manually abandoned / cap reached).
    gen_attempts = Column(Integer, nullable=False, server_default="0", default=0)
    gen_next_retry_at = Column(DateTime, nullable=True, index=True)
    # 2026-08-04 — review results of the multi-agent refinement pipeline (services/content_quality.py):
    # quality_score is a 0-100 weighted total (None = review not run / review failed),
    # quality_json details {dims, uncovered, blocking, fixes, rounds, notes}, for read-only display.
    quality_score = Column(Integer, nullable=True)
    quality_json = Column(Text, nullable=True)

    status = Column(String, nullable=False, default="draft")
    # ↑ generating (LLM writing, since 2026-07-31) / draft (user-saved draft) /
    #   pending_review (ticked into review by admin) / approved / rejected / published
    selected_for_review = Column(Boolean, nullable=False, default=False)
    review_decision_at = Column(DateTime, nullable=True)
    reviewer_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reject_reason = Column(Text, nullable=True)
    # [{platform: "抖音"|"小红书"|"视频号"|"公众号" (Douyin|Xiaohongshu|WeChat Channels|WeChat official account), media: "...", url?, marked_at, marked_by}]
    publish_targets_json = Column(Text, nullable=False, default="[]")
    # v3.1 — content source (2026-05-17): 'ai' = AI automatic / admin-triggered generation; 'user' = user-supplied draft
    source = Column(String(length=8), nullable=False, default="ai", index=True)

    # URL-level ROI hits (2026-05-19): when publish_targets[].url appears in an AI answer's citations_json,
    # record the list of response_ids per engine. Empty `{}` = not yet cited by any engine.
    # Shape: {"deepseek": [response_id, ...], "doubao": [...], ...}
    cited_by_json = Column(Text, nullable=False, default="{}")

    # ─── Platform review result backfill (2026-06-12) ────────────────────────────────
    # After ops publish a draft to a platform, they fill in the platform's review result; reasons for rejected ones are
    # aggregated and distilled by platform_rules' "learn from rejections" into rule increments for that platform.
    platform_review_status = Column(String(length=16), nullable=True)   # passed / rejected
    platform_reject_reason = Column(Text, nullable=True)

    # ─── Mediumsly push status (2026-05-29) ─────────────────────────────
    # Written by services/mediumsly_publisher.py, read by the publish endpoint. Decoupled from
    # publish_targets_json: the latter is manual admin marking; these 4 columns track automatic outbound results.
    #   - with mediumsly_post_id → publisher PATCHes in place
    #   - without → publisher POSTs a new one (Mediumsly auto-upserts the user by email)
    #   - PATCH gets 404 → publisher clears mediumsly_post_id + writes the error, and the next retry automatically falls back to POST
    # See docs/05-geo-integration.md (mediumsly repo).
    mediumsly_post_id    = Column(String(length=64),  nullable=True)
    mediumsly_url        = Column(String(length=512), nullable=True)
    mediumsly_pushed_at  = Column(DateTime,           nullable=True)
    mediumsly_last_error = Column(Text,               nullable=True)

    # ─── Content labeling and generation provenance (2026-08-16) ──────────────────────────────
    # Where the implicit labels of the "Measures for Labeling AI-Generated Synthetic Content" land; item 5 of the algorithm filing correction list.
    # The three columns are written uniformly by services/content_marking.stamp(), **only once when empty**:
    # regeneration does not change the owner or the first generation IP, otherwise the ledger would be overwritten with false data by later regenerations.
    #   gen_user_id   — who triggered generation; automatic batch runs have no trigger person, falling back to the topic owner
    #   gen_client_ip — the trigger's IP (behind nginx, the first hop of X-Forwarded-For);
    #                   'system' = automatic batch run, used by the provenance page to distinguish manual/automatic
    #   gen_trace_no  — content number VGL-<entity code>-<yyyymmdd>-<doc.id six digits>,
    #                   also embedded in the body as a zero-width character watermark, still decodable after reposting
    gen_user_id   = Column(Integer,          nullable=True)
    gen_client_ip = Column(String(length=64), nullable=True)
    gen_trace_no  = Column(String(length=48), nullable=True)

    # ─── Publishing term-selection ledger (2026-08-17) ────────────────────────────────────
    # Before these two columns, "which set of terms this article was originally written for" had no authoritative version: only the single-valued
    # source_query_text (user-editable) and a uuid pointing to a row in plan.publishing_plan_json,
    # and that JSON is fully rewritten on every PUT / reorder / renewal, so the link breaks completely once a row uuid changes.
    # So DocRelationBlock / DocWhy / describe_generation_inputs were all **recomputed after the fact**.
    #   gen_batch_id      — which generation trigger this article belongs to (batch table: TopicContentGenBatchORM)
    #   gen_queries_json  — snapshot list[str] of the terms **this article** actually covers (the batch's terms are the union; this is a subset)
    # Both columns follow the same discipline as the three gen_* columns: **written only once when empty**, not overwritten on regeneration — regeneration makes no term-selection decision,
    # and this answers "why it was originally written". "Which articles this run produced" is in the batch row's doc_ids_json.
    gen_batch_id     = Column(Integer, ForeignKey("topic_content_gen_batches.id", ondelete="SET NULL"),
                              nullable=True)
    gen_queries_json = Column(Text, nullable=True)


class TopicContentGenBatchORM(Base):
    """Publishing batch ledger (2026-08-17) — one row per "generation trigger", recording which terms were selected.

    Why a separate table rather than only on drafts: a batch may fail to generate any article at all,
    and with drafts only, the whole term-selection action would be invisible afterwards. The selection event itself is a fact to be recorded.

    trace_id reuses the batch-level id minted by usage_ctx in content_generator._run_generation_safe
    (propagated through spawn_thread's contextvar snapshot into every LLM call of the batch, already persisted in llm_calls.trace_id
    and indexed) — so "how much this batch cost and how many model calls it made" comes for free, with no separate batch number needed.

    Empty actor_id = triggered by no one (cron / Siyuan / failure retry). **Does not fall back to the topic owner**:
    this is an operations ledger; writing "Zhang San triggered a batch at 03:00" while Zhang San was asleep is a forged record
    (unlike gen_user_id's "owner" semantics, whose fallback is required by the "Labeling Measures").
    """
    __tablename__ = "topic_content_gen_batches"
    __table_args__ = (
        Index("idx_tcgb_topic_created", "topic_id", "created_at"),
        Index("idx_tcgb_trace", "trace_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"), nullable=False)
    operation_context_id = Column(
        Integer, ForeignKey("operation_contexts.id", ondelete="RESTRICT"), nullable=True,
        index=True,
    )
    trace_id = Column(String(length=64), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    actor_id = Column(Integer, nullable=True)
    # plan_confirm / plan_confirm_incr / regenerate_item / plan_item_add / batch_generate /
    # run_now / cron / siyuan / agent / unknown
    trigger_source = Column(String(length=24), nullable=False, default="unknown")
    plan_id = Column(Integer, ForeignKey("ai_telemetry_topic_execution_plans.id", ondelete="SET NULL"),
                     nullable=True)
    queries_json = Column(Text, nullable=False, default="[]")   # All terms selected this time (union of each draft's snapshot)
    doc_ids_json = Column(Text, nullable=False, default="[]")   # Drafts touched by this batch (new + regenerated)
    doc_count = Column(Integer, nullable=False, default=0)
    stats_json = Column(Text, nullable=True)                    # {"created": 3, "regenerated": 2}
    note = Column(Text, nullable=True)


class PlatformRuleORM(Base):
    """2026-06-12 — platform review rule library: one "review red lines" text stored per publishing platform.

    rules_text is injected into the prompt by the publishing row's platform when generating articles (violating any rule makes the draft unusable);
    pending_rules_text is "learned from rejections" (rule increments distilled by the LLM from the platform's aggregated platform_reject_reason),
    merged into rules_text after admin adoption, with pending cleared.
    """
    __tablename__ = "platform_rules"
    __table_args__ = (Index("idx_platform_rules_platform", "platform", unique=True),)

    id = Column(Integer, primary_key=True, index=True)
    platform = Column(String(length=32), nullable=False)
    rules_text = Column(Text, nullable=False, default="")
    pending_rules_text = Column(Text, nullable=False, default="")
    learned_at = Column(DateTime, nullable=True)        # Time of the latest "learn from rejections"
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_by_id = Column(Integer, nullable=True)


class TopicMediaORM(Base):
    """v3.2 (2026-05-18) — image / video assets uploaded by a user for a topic.

    Two kinds of things in the profile upload dialog:
      - text (.txt/.md/.docx/PDF): goes through /profile/extract*, the LLM extracts and backfills the profile form directly,
        original files not persisted;
      - media (.jpg/.png/.mp4/...): goes through this table — written to disk + one DB registration row,
        referenced later as an asset library when generating content / publishing.

    storage_path is the relative path data/topic_media/{topic_id}/{uuid}.{ext};
    GET /media/{id}/blob joins it back to the root directory when streaming.
    """
    __tablename__ = "topic_media"
    __table_args__ = (
        Index("idx_topic_media_topic", "topic_id"),
        Index("idx_topic_media_user", "user_id"),
        # Reverse-lookup entry for image content numbers, same as for text drafts: the number contains media.id, naturally unique
        Index("idx_topic_media_trace_no", "gen_trace_no", unique=True),
        Index("idx_topic_media_ext_risk", "external_risk"),
        Index("idx_topic_media_topic_hash", "topic_id", "content_hash"),
        Index("idx_topic_media_generation_state_uploaded", "generation_state", "uploaded_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    filename = Column(String(length=255), nullable=False, default="")  # Original file name at user upload
    kind = Column(String(length=8), nullable=False, default="image")    # "image" | "video"
    mime = Column(String(length=128), nullable=False, default="")
    size = Column(Integer, nullable=False, default=0)                   # Bytes
    storage_path = Column(String(length=512), nullable=False, default="")
    uploaded_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    use_count = Column(Integer, nullable=False, server_default="0", default=0)
    claim_token = Column(String(length=32), nullable=True)
    claim_expires_at = Column(DateTime, nullable=True)
    image_edit_fail_count = Column(Integer, nullable=False, server_default="0", default=0)
    image_edit_retry_at = Column(DateTime, nullable=True)

    # ─── Description and dedup (2026-09-16) ──────────────────────────────────────
    #   description  — what this image is (filled at upload / edited later)
    #   content_hash — content sha256 computed at upload. Only one row per topic per content; dragging in a duplicate
    #                  no longer stores another copy or sends another external check
    description  = Column(String(length=500), nullable=False, server_default="", default="")
    content_hash = Column(String(length=64), nullable=True)

    # ─── Image content labeling (2026-08-16) ────────────────────────────────────
    # Image side of the "Measures for Labeling AI-Generated Synthetic Content", items 7 and 8 of the algorithm filing correction list.
    # Only AI-generated images (from services/image_gen.py) are labeled — assets uploaded by users
    # are not generated/synthesized content, and drawing 「内容由AI生成」 ("AI-generated content") on them would be false labeling.
    #   is_ai_generated — whether generated by this platform; previously could only be guessed from the filename prefix "AI配图-" ("AI image-"), unreliable
    #   gen_model       — image model (wanx2.1-t2i-turbo), also written into file metadata
    #   gen_trace_no    — image content number VGI-<entity code>-<yyyymmdd>-<media.id six digits>,
    #                     the same value in three places: visible watermark, EXIF/tEXt metadata
    is_ai_generated = Column(Boolean, nullable=False, server_default="0", default=False)
    gen_model    = Column(String(length=64), nullable=True)
    gen_trace_no = Column(String(length=48), nullable=True)
    source_media_id = Column(
        Integer, ForeignKey("topic_media.id", ondelete="SET NULL"), nullable=True)
    generated_doc_id = Column(
        Integer, ForeignKey("topic_generated_docs.id", ondelete="SET NULL"), nullable=True)
    generation_state = Column(String(length=16), nullable=True)  # pending / attached / orphaned

    # ─── Image external-check soft signals (2026-08-18) ──────────────────────────────────
    # Aliyun Content Security ImageModeration, the image side of correction list items 9 (third-party review) and 10 (manual review).
    # Same definition as the text side: **soft signals, never blocking any pipeline**, only as reference for manual review.
    #   external_json — the full return of moderate_image() (status/labels/descriptions/request_ids)
    #   external_risk — redundant risk level, for filtering on the asset library list page without parsing JSON per row
    external_json = Column(Text, nullable=True)
    external_risk = Column(String(length=16), nullable=True)


class TopicDocPublishTaskORM(Base):
    """Flywheel P1 (§5.3) — one placement task of a draft on one media/account + URL backfill state machine.

    Why a separate table: one draft can be placed on several media, and the main draft table only handles content; each media's placement
    status (to publish / pending backfill / placed / failed) is carried by this table. Core invariants (§5.2/§6):
      · a task without published_url cannot enter published;
      · only published tasks register retests and enter the reward window.
    Coexists short-term with topic_generated_docs.publish_targets_json: after published, it syncs back to
    publish_targets_json so the existing backfill_cited_by / frontend display keep working (§5.3.8).
    """
    __tablename__ = "topic_doc_publish_tasks"
    __table_args__ = (
        Index("idx_publish_task_topic_status", "topic_id", "status"),
        Index("idx_publish_task_doc", "doc_id"),
        Index("idx_publish_task_url_canonical", "url_canonical"),
        Index("idx_publish_task_assignee", "assigned_to_user_id", "status"),
        # Self-hosted pool idempotency: one draft can have only one task on the same account of the same platform.
        # Partial unique (only effective when publish_account_id is non-null) — supported by both PG / SQLite.
        Index("uq_publish_task_doc_platform_account", "doc_id", "platform", "publish_account_id",
              unique=True,
              postgresql_where=text("publish_account_id IS NOT NULL"),
              sqlite_where=text("publish_account_id IS NOT NULL")),
        Index("uq_publish_task_doc_aitoearn_binding", "doc_id", "aitoearn_binding_id",
              unique=True,
              postgresql_where=text("aitoearn_binding_id IS NOT NULL AND status != 'cancelled'"),
              sqlite_where=text("aitoearn_binding_id IS NOT NULL AND status != 'cancelled'")),
    )

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("ai_telemetry_topics.id", ondelete="CASCADE"), nullable=False)
    doc_id = Column(Integer, ForeignKey("topic_generated_docs.id", ondelete="CASCADE"), nullable=False)
    operation_context_id = Column(
        Integer, ForeignKey("operation_contexts.id", ondelete="RESTRICT"), nullable=True,
        index=True,
    )
    vscl_action_id = Column(Integer, nullable=True)   # Siyuan action tracing (§4.2)

    platform = Column(String(length=32), nullable=False, default="")     # 公众号 / 知乎 / 小红书 / Mediumsly / 媒体站 (WeChat official account / Zhihu / Xiaohongshu / Mediumsly / media site)
    media_name = Column(String(length=128), nullable=False, default="")  # Specific account name / media name / resource name
    # Account binding for the self-hosted pool (server_pool channel). Always NULL for existing rows.
    # The idempotency unique constraint (doc_id, platform, publish_account_id) is a **partial unique index**
    # (WHERE publish_account_id IS NOT NULL), so NULL rows are unconstrained and historical data is unaffected.
    # Why this constraint is required: the three channels' account identities come from different sources, and mixing them breaks idempotency, showing up as
    # the same article published twice on the same account — an irreversible error (plan §1.2 / F5 item 1).
    publish_account_id = Column(Integer, nullable=True)
    # AiToEarn personal account identity. The upstream id stays in the binding
    # table and is never accepted from a regular user's request.
    aitoearn_binding_id = Column(
        Integer, ForeignKey("aitoearn_account_bindings.id", ondelete="SET NULL"), nullable=True)
    channel_type = Column(String(length=16), nullable=False, default="manual_account")
    # ↑ auto_account (can auto-publish) / manual_account (has an account, manual) / external_media (external/paid media)
    publish_mode = Column(String(length=16), nullable=False, default="manual")  # auto / manual / paid_media
    status = Column(String(length=16), nullable=False, default="pending_url")
    # ↑ planned / pending_publish / publishing / pending_url / draft_saved / url_submitted / published / failed / cancelled

    planned_publish_at = Column(DateTime, nullable=True)
    actual_published_at = Column(DateTime, nullable=True)
    published_url = Column(String(length=1024), nullable=True)
    url_canonical = Column(String(length=1024), nullable=True)   # Normalized, for dedup and attribution

    assigned_to_user_id = Column(Integer, nullable=True)   # Person responsible for manual publishing
    submitted_by_user_id = Column(Integer, nullable=True)  # Person who backfilled the URL
    submitted_at = Column(DateTime, nullable=True)
    verified_at = Column(DateTime, nullable=True)

    proof_json = Column(Text, nullable=True)          # {note, order_no, screenshot_url}
    retest_probe_id = Column(Integer, nullable=True)  # Corresponding GeoRetestProbeORM
    last_error = Column(Text, nullable=True)

    # Paid media channel (Ruanwenjiang etc.) extension: resource snapshot / draft_id / order identifier / price etc.
    # {channel, type, resource_id, resource_title, draft_id, delivery,
    #  quoted_price, quoted_level, quoted_at, publish_started_at, order_no?}
    channel_meta_json = Column(Text, nullable=True)
    cost_cents = Column(Integer, nullable=True)       # Amount actually paid (cents), for report aggregation

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


# ─────────────────────────── Schemas ──────────────────────────


VALID_ENGINES = {
    "deepseek", "doubao", "qwen", "wenxin", "yuanbao",
    "chatgpt", "claude", "gemini",
    # Anonymous physical-device channels (tasks claimed by V-networks/workers/desktop-cn): do not use the account pool, coexist with their canonical engines.
    # Doubao must use physical devices — the server side cannot obtain passport trust credentials (see V-networks/workers/desktop-cn/README.md);
    # Wenxin/Qwen run on physical devices to get residential/office egress IPs and real device fingerprints.
    "doubao_browser", "wenxin_browser", "qwen_browser",
    # yuanbao_browser (2026-09-21): Yuanbao's physical-device channel. **It is not anonymous** — the Yuanbao site has no anonymous
    # mode, so the physical device also leases a login session (dist-worker/session_lease.py); it shares only the executor with the three
    # above, and differs in whether it uses the account pool. The main channel is still yuanbao (server account pool).
    "yuanbao_browser",
    # kimi (2026-09-18): browser-service pure HTTP (Connect API) + account-pool token, no browser.
    "kimi",
    # Overseas physical-device channels (tasks claimed by V-networks/workers/desktop-global): coexist with gemini / chatgpt /
    # claude via the OpenRouter API; perplexity only has a physical-device channel. aio_browser etc. will be added once the center has an aio_absent result type.
    "gemini_browser", "perplexity_browser", "chatgpt_browser", "claude_browser",
    # Mobile app channels (2026-09-23, tasks claimed by V-networks/workers/emulator-cn, region=android):
    # collect from each vendor's **native app** in a rooted Android emulator, not using the engine_sessions account pool.
    # These, *_browser (desktop physical-device browser) and doubao_mobile (Doubao mobile web via pool-enforced browser automation)
    # have capacity / quality / rate limits computed separately by engine key in the dispatch center — names must not be mixed,
    # see APP_ENGINES in V-networks/worker-center/app/batch/runner.py.
    # diandian_app (Xiaohongshu Diandian) only exists as an app and is not a channel variant of these 6 models: it is outside
    # the 6-model enum in frontend constants/engines.ts, so only ops can attach it to topics,
    # and users cannot tick it in their panel (see user_selectable_engine_keys in api/ai_telemetry.py).
    "doubao_app", "qwen_app", "deepseek_app", "wenxin_app", "yuanbao_app", "kimi_app",
    "diandian_app",
}


class ClusterMeta(BaseModel):
    cluster_id: int
    label: str
    size: int


# Phase C — review freeze: review state machine for seed terms + queries
ReviewStatus = str  # "pending" | "approved" | "rejected"

# 2026-05-28 — 4-dimensional scene expansion: the same seed calls 4 LLM templates in parallel to produce 4 kinds of long-tail pools
# The seed itself is neutral; scene_type is only attached to the produced QueryItems
# 2026-07-31 — completed the five seed term types: compare had long been running in the data (synthetic scripts write it, and the report read side's
# EXPOSURE_ONLY_SCENES and term-type labels recognize it), but it was missing from the enum, so TopicOut silently downgraded existing
# compare to search when returning to the frontend — adding it is a bug fix, not a feature.
# scenario (scenario terms) is a newly added sixth type, produced only by direct labeling in query-expansion templates and diagnosis term generation.
SceneType = Literal["search", "qa", "intent", "brand", "compare", "scenario"]
DEFAULT_SCENE: SceneType = "search"
ALL_SCENES: tuple[SceneType, ...] = ("search", "qa", "intent", "brand", "compare", "scenario")

# Default fan-out set for query expansion, separate from the "valid value set".
# ALL_SCENES used to serve both roles; adding values directly would turn each expansion from 4 LLM calls into 6
# (cost and latency +50%), and nobody would notice. New categories join query expansion only when explicitly ticked by the frontend.
EXPAND_SCENES: tuple[SceneType, ...] = ("search", "qa", "intent", "brand")


class SeedPromptItem(BaseModel):
    text: str
    status: ReviewStatus = "pending"
    submitted_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    rejected_at: Optional[datetime] = None
    reviewer_id: Optional[int] = None


class QueryItem(BaseModel):
    text: str
    cluster_id: Optional[int] = None
    # Phase C review freeze: legacy data defaults to "approved" during migration
    status: ReviewStatus = "approved"
    submitted_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    reviewer_id: Optional[int] = None
    # Phase D monitoring selection: the ≤50 the user ticks from the expansion pool → selected=True; legacy data defaults to True
    selected: bool = True
    # 2026-05-20 — which seed prompt the user expanded this query from; empty string means legacy or not recorded
    seed: str = ""
    # 2026-05-28 — labels produced by 4-dimensional scene expansion: search/qa/intent/brand
    # legacy data defaults to search on deserialization
    scene_type: SceneType = DEFAULT_SCENE
    # ★ 2026-08-17 — soft delete. **Set a flag; elements are never removed from queries_json** (missing = not deleted).
    #   Why not pop: the report definition's _scene_of_map and the detail table's _scene_by_query both look up scene_type
    #   by **term text**. Once an element is removed, this term's historical "ask about the brand, answer with the brand" responses cannot be found
    #   and get counted as category terms in the exposure denominator — deleting a term would inflate exposure, the exact opposite.
    #   Also, text is the only identity (no numeric id); once the dict is gone, cluster_id/seed/is_seed/locked/scene_type
    #   are all irreversibly lost, with no way to restore. Always decide via active_query_texts / deleted_query_texts.
    deleted_at: Optional[datetime] = None
    deleted_by_id: Optional[int] = None


# ─────────────── Phase D — brand profile (6 modules) ──────────────


class CtripLocation(BaseModel):
    """Structured location for Ctrip publishing. name is used for search, city/address to disambiguate same-named POIs."""
    name: str = Field("", max_length=256)
    city: str = Field("", max_length=128)
    address: str = Field("", max_length=512)


# Separators for alias / tag strings: split when the LLM or user writes aliases as one string like "A/B、C"
_PRODUCT_ALIAS_SEP = re.compile(r"[,,、;;/|\n]+")


class BrandProduct(BaseModel):
    """A product / model in section 3 of the brand profile (added 2026-09-22, for needs like a brand tracking a specific gamepad model).

    Stored in profile_json.products[], no new columns. All fields have defaults — TopicOut.from_orm_row falls back to an empty form
    with a bare except for the whole BrandProfile, so one bad product row must not blank the entire profile.

    name / aliases are used as **brand terms** (hit judgment + metric term exclusion + expansion classification), see profile_product_terms;
    category is the generic category name ("gamepad"); during expansion, when a seed hits a product, category terms are built from category.
    """
    name: str = Field("", max_length=64)                                        # Product / model name
    aliases: list[str] = Field(default_factory=list, max_length=10)             # Aliases / model variants
    category: str = Field("", max_length=64)                                    # Generic category name
    selling_points: list[str] = Field(default_factory=list, max_length=10)      # Core selling points
    target_users: list[str] = Field(default_factory=list, max_length=10)        # Target audience / scenarios
    is_featured: bool = False                                                   # Featured
    launch_date: str = Field("", max_length=32)                                 # Launch date (free text)
    price_range: str = Field("", max_length=64)                                 # Price range
    notes: str = Field("", max_length=500)                                      # Notes

    @field_validator("aliases", "selling_points", "target_users", mode="before")
    @classmethod
    def _split_str_list(cls, v):
        if isinstance(v, str):
            return [x.strip() for x in _PRODUCT_ALIAS_SEP.split(v) if x.strip()]
        if isinstance(v, list):
            return [str(x).strip() for x in v if x is not None and str(x).strip()]
        return []

    @field_validator("name", "category", "launch_date", "price_range", "notes", mode="before")
    @classmethod
    def _scalar_to_str(cls, v):
        """LLM extraction often gives launch date / price as numbers (2024 / 299); pydantic v2 does no int→str,
        and one invalid field drops the whole product — so convert to string first; None → empty string."""
        if v is None:
            return ""
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return str(v)
        if isinstance(v, list):
            return " / ".join(str(x).strip() for x in v if x is not None and str(x).strip())
        return v if isinstance(v, str) else str(v)

    @field_validator("is_featured", mode="before")
    @classmethod
    def _featured_to_bool(cls, v):
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "y", "是", "主推", "旗舰")
        return bool(v) if v is not None else False


class BrandProfile(BaseModel):
    """The 6-module form users fill when submitting their profile.

    The profile maps one-to-one to a topic, serialized into topic.profile_json.
    Required fields are given by each field's Field annotation (min_length / defaults); the submit-for-review endpoint does full validation again.
    """
    # 1. Basic profile identity
    profile_name: str = Field("", max_length=128)               # Profile name
    company_full_name: str = Field("", max_length=256)          # Full company / brand name
    company_short_name: str = Field("", max_length=64)          # Short company / brand name
    industry: str = Field("", max_length=128)                   # Industry / sector
    # Entity type — drives the style of expanded terms (service tool → features/price/integration; manufacturer → factory/specs/OEM).
    # Values: service_tool / manufacturer / brand_owner / other / "" (judged by AI during profile extraction, manually editable)
    entity_type: str = Field("", max_length=32)
    core_business_lines: list[str] = Field(default_factory=list, max_length=20)  # Core business lines (multi-select)
    service_geo: str = Field("", max_length=256)                # Service region
    ctrip_location: CtripLocation = Field(default_factory=CtripLocation)  # Ctrip POI auto-match
    website: str = Field("", max_length=512)                    # Brand website (optional; if filled the audit report reuses it directly, otherwise on-site technical diagnosis is skipped)
    # Contact details (optional) — added 2026-08-10. Phone numbers/URLs in drafts used to be freely generated by the model,
    # i.e. made up; these three + website are the **only** whitelist of contact details allowed in the body,
    # and when none is filled the generation side forbids writing any contact details (see content_generator._build_contact_block).
    contact_phone: str = Field("", max_length=64)               # Contact phone / 400 number
    contact_wechat: str = Field("", max_length=128)             # WeChat ID / official account / WeCom
    contact_email: str = Field("", max_length=128)              # Contact email

    # 2. Content creation direction (all multi-select)
    creation_directions: list[str] = Field(default_factory=list, max_length=20)  # Creation directions
    copywriting_types: list[str] = Field(default_factory=list, max_length=20)    # Preferred copywriting types
    target_platforms: list[str] = Field(default_factory=list, max_length=20)     # Target platforms
    content_tones: list[str] = Field(default_factory=list, max_length=20)        # Preferred content tones
    content_redlines: list[str] = Field(default_factory=list, max_length=20)     # Content no-go zones / prohibitions (optional)

    # 3. Brand entity information
    team_size: str = Field("", max_length=64)                                    # Company / team size (optional)
    founded_year: str = Field("", max_length=64)                                 # Founding date / years in business
    core_credentials: list[str] = Field(default_factory=list, max_length=60)     # Core honors / endorsing qualifications
    brand_diff_tags: list[str] = Field(default_factory=list, max_length=10)      # Brand differentiation tags (3-5)

    # 4. Core product / service information
    core_service_overview: str = Field("", max_length=2000)                      # Core service overview
    service_features: list[str] = Field(default_factory=list, max_length=20)     # Core service features
    service_process: list[str] = Field(default_factory=list, max_length=20)      # Key service process / steps
    target_scenarios: list[str] = Field(default_factory=list, max_length=20)     # Covered scenarios / customer types
    service_guarantees: list[str] = Field(default_factory=list, max_length=20)   # Service delivery guarantees (optional)
    # Product / model list (added 2026-09-22) — consumer brands monitor and generate drafts per specific product; is_featured marks featured ones
    products: list[BrandProduct] = Field(default_factory=list, max_length=30)

    @field_validator("products", mode="before")
    @classmethod
    def _drop_bad_products(cls, v):
        """Drop non-dict items / items without a name; a single bad field is handled by BrandProduct itself."""
        if not isinstance(v, list):
            return []
        out = []
        for x in v:
            if isinstance(x, BrandProduct):
                x = x.model_dump()
            if isinstance(x, dict) and str(x.get("name") or "").strip():
                out.append(x)
        return out[:30]

    # 4. Brand story and emotional material (formerly 5; the "target users and pain points" section was removed on 2026-05-17)
    brand_story: str = Field("", max_length=2000)                                # Brand story / founding motivation
    key_person_story: str = Field("", max_length=2000)                           # Key person story (optional)
    case_stories: list[str] = Field(default_factory=list, max_length=60)         # Typical cases (optional)
    brand_values: str = Field("", max_length=1000)                               # Brand values / service philosophy

    # 5. Supplementary material and creative boundaries (formerly 7)
    available_materials: list[str] = Field(default_factory=list, max_length=20)  # Types of material available (optional)
    brand_slogan: str = Field("", max_length=256)                                # Slogan / tagline (optional)
    core_message: str = Field("", max_length=1000)                               # Core message of this content
    extra_notes: str = Field("", max_length=2000)                                # Other notes (optional)


# Required profile fields — used by submit-for-review validation.
# The whole "content creation direction" section (creation_directions / copywriting_types / target_platforms /
# content_tones / content_redlines) was removed from the profile form on 2026-05-17 — it is decided in the
# "content publishing strategy" stage and no longer enforced in the profile. Schema fields kept for old-data compatibility.
# Since 2026-05-17 — only the 6 fields of section "1. basic identity" are required.
# 2026-05-27: core_business_lines is no longer required — in practice many cases describe business lines in one sentence
# within industry, so empty is allowed to pass. The other 5 remain required.
# Other modules (brand entity / products and services / brand story / supplementary material) are all optional:
# user-uploaded profiles are the material library for later drafts; filling more is encouraged but not forced; the LLM uses whatever it gets.
PROFILE_REQUIRED_FIELDS: tuple[str, ...] = (
    "profile_name", "company_full_name", "company_short_name",
    "industry", "service_geo",
)


class TopicChangelogEntry(BaseModel):
    at: datetime
    actor_id: Optional[int] = None
    actor_role: str = "user"                # user / admin / system
    field: str
    before: Optional[str] = None
    after: Optional[str] = None
    note: Optional[str] = None
    # The topic version after this changelog entry was written. Old entries (before the migration) lack it; may be omitted
    version: Optional[int] = None


class ExpansionLogEntry(BaseModel):
    at: datetime
    seed: str = ""
    model: str = ""
    expanded_count: int = 0
    raw_excerpt: str = ""                   # Truncated to the first 300 characters


class SubmitForReviewPayload(BaseModel):
    """Submit for review — validates: required profile fields complete + ≥1 pending/approved seed + ≤50 selected queries."""
    pass


# Cap on selected prompts (50 → 200 since 2026-05-20; 200 → 1000 since 2026-08-05)
MAX_SELECTED_QUERIES = 1000
# Cap on candidates per seed expansion = per-scene cap × 4 default scenes; users accumulate over several expansions, then tick ≤ MAX_SELECTED_QUERIES.
# 2026-09-21 200 → 120 (per scene 50 → 30): neolink's deepseek-v4-flash takes around 50 seconds per call,
# and intent / brand scene prompts are longer, producing 5000+ tokens, nearly always hitting GEO_EXPANSION_TIMEOUT=90s —
# since 09-17 the intent scene had 0 successes (32 timeouts), and users just saw "no intent terms".
# Cutting the count by 40% cuts output tokens accordingly, bringing single calls back under the timeout; for more terms, expand a few more rounds.
MAX_EXPANSION_CANDIDATES = 120


class MonitoredQueryItem(BaseModel):
    """Input item when the user ticks/unticks a prompt."""
    text: str
    selected: bool
    # 2026-05-20 — which seed term this query was expanded from (available at the candidate stage),
    # persisted together when selected, so later reports / query details render grouped by seed.
    seed: str = ""
    # 2026-05-28 — 4-dimensional scene label (search/qa/intent/brand), passed by the frontend when ticking in SceneExpander.
    # When legacy / old clients omit it, the backend falls back to search.
    scene_type: SceneType = DEFAULT_SCENE


class SelectedQueriesPayload(BaseModel):
    items: list[MonitoredQueryItem] = Field(..., max_length=2000)


class ProfileExtractPayload(BaseModel):
    """AI smart fill — raw text from files the user dragged in or pasted."""
    text: str = Field(..., min_length=10, max_length=60000)
    enterprise_user_id: Optional[int] = None
    # 2026-09-22 — the full original text also goes into the vector store (agent_materials). Empty topic_id = wizard stage, attached at account level;
    # persist=False only extracts the form without persisting (rarely used, kept for debugging).
    topic_id: Optional[int] = None
    persist: bool = True


class MaterialDocOut(BaseModel):
    """One document in the vector store (aggregating the N chunks of the same source)."""
    source: str
    topic_id: Optional[int] = None
    title: str
    chunks: int
    chars: int
    embedded: int
    created_at: Optional[datetime] = None


class ProfileExtractOut(BaseModel):
    profile: BrandProfile
    used_model: str
    # Seed prompt candidates the LLM provides along the way (3-8). Frontend step 2 pre-fills with them; users can keep/delete/edit.
    seed_suggestions: list[str] = Field(default_factory=list)
    # Result of putting the original text into the vector store; persist=False or failed persistence → None (extraction itself unaffected)
    material: Optional[dict] = None


# ─────────────── Phase D — execution plan / content documents ──────────────


class TopicProgressCell(BaseModel):
    query: str
    engine: str
    status: str                          # pending / running / done
    hit: Optional[bool] = None
    last_checked_at: Optional[datetime] = None


class PublishPlanItem(BaseModel):
    """A single publishing plan row (GET output shape).

    Persisted fields: id / seq / publish_date / seed / query / template_id / platform / note.
    Other fields are derived values aggregated / joined live at GET time and not stored in publishing_plan_json.

    New seed-based flow: each approved seed × 5 system templates = 5 rows; the 5 rows fall on the same day.
    Legacy query-based: one row per monitored query, query non-empty, seed None.
    Content generation prefers seed (falls back to query if empty), and hit rate aggregation also prefers seed.

    Priority:
      - high: monitoring hit rate 0% (the AI does not mention the brand at all; content needed)
      - med:  0 < hit rate < 50% (partial hits, reinforce)
      - low:  >= 50% (already covered; may skip or publish rarely)
    """
    # Persisted fields
    id: str                               # uuid; stable id for frontend editing, also used to write back doc.plan_item_id
    seq: int = 0                          # Display order
    publish_date: str                     # YYYY-MM-DD or YYYY-MM-DDTHH:MM (minute-level scheduling)
    seed: Optional[str] = None            # Seed prompt text (seed-based rows; None for legacy rows)
    query: str = ""                       # Monitored query (legacy single-select rows; new rows use queries multi-select)
    # 2026-06-11 — one article can attach several monitored queries (multi-select); the body must naturally cover this set of questions.
    # Legacy rows only have the single query; when both exist queries takes precedence.
    queries: list[str] = Field(default_factory=list)
    template_id: Optional[int] = None     # None for old-data fallback
    platform: Optional[str] = None
    # Which placement channel: "" / aitoearn own authorized accounts / own self-hosted site / rwj Ruanwenjiang paid proxy publishing.
    # platform is always the **real platform name** (Sohu Hao / Zhihu); the channel is recorded separately — writing rules follow the platform,
    # and filling platform with 「软文匠」 (Ruanwenjiang) would generate content under the wrong platform's rules.
    channel: Optional[str] = None
    # 2026-08-04 — additional placement platforms for the same draft (multi-platform posting). platform is the main platform (writing rules follow it);
    # extra_platforms only affects how many placement tasks are created after approval (services/publish_preset.py).
    extra_platforms: list[str] = Field(default_factory=list)
    # Preset placement media (2026-07-28): on approval, placement tasks are created automatically per (platform, media);
    # with channel=rwj, rwj_resource_id is used for Ruanwenjiang proxy publishing (services/publish_preset.py)
    media: Optional[str] = None
    rwj_resource_id: Optional[int] = None
    note: Optional[str] = None
    # 2026-06-12 — per-row creative preference (the initial plan assigns by template kind; when empty, generation uses the profile default)
    creation_directions: list[str] = Field(default_factory=list)
    copywriting_types: list[str] = Field(default_factory=list)
    # Compatibility fields (old frontends reading old data)
    day: int = 0                          # Day number (0 = today); the new version computes by seq
    # Computed at GET time
    coverage_pct: float = 0.0
    priority: str = "low"                 # high / med / low
    doc_id: Optional[int] = None
    doc_status: Optional[str] = None
    # 2026-08-21 — failed draft rows still have status pending_review (failures must also leave generating,
    # see content_generator), so looking only at doc_id / doc_status would treat them as "draft produced". The plan page
    # must tell them apart, or a row that wrote nothing shows as a success.
    doc_failed: bool = False
    doc_error: Optional[str] = None       # Error summary (truncated, human-readable)
    # For template display (joined with ContentTemplateORM)
    template_name: Optional[str] = None
    template_kind: Optional[str] = None
    # Template default platform list (for the platform dropdown)
    suggested_platforms: list[str] = Field(default_factory=list)
    # 2026-08-06 — scheduling basis (machine-written, read-only): why this row's platform/template/creative preference/prompts/
    # seed/media are each what they are. An item manually changed in the editor is downgraded to
    # {"reason":"manual"}. Structure in services/plan_basis.row_why.
    # **Not in PlanItemDraft** — the frontend does not send machine fields back; on PUT the backend passes them through from the old row.
    why: Optional[dict] = None
    # How this row came about: strategy plan layer N / missed-question backflow / candidate topic insertion / manual
    origin: Optional[dict] = None


class PlanItemDraft(BaseModel):
    """Draft publishing rows sent by the frontend on PUT /execution-plan.

    id is optional: None means a new row and the backend fills in a uuid. At least one of seed / query must be non-empty.
    New seed-based rows: seed non-empty, query empty;
    legacy single-query rows: query ∈ monitored, seed empty.
    """
    id: Optional[str] = None
    seq: int = 0
    publish_date: str                     # YYYY-MM-DD or YYYY-MM-DDTHH:MM
    seed: Optional[str] = None
    query: str = ""
    # 2026-06-11 — multi-select monitored queries; each must be ∈ monitored. Coexists with the single query; queries takes precedence.
    queries: list[str] = Field(default_factory=list, max_length=50)
    template_id: int
    platform: str
    # Which placement channel: "" / aitoearn own authorized accounts / own self-hosted site / rwj Ruanwenjiang paid proxy publishing.
    # platform is always the **real platform name** (Sohu Hao / Zhihu); the channel is recorded separately — writing rules follow the platform,
    # and filling platform with 「软文匠」 (Ruanwenjiang) would generate content under the wrong platform's rules.
    channel: Optional[str] = None
    # 2026-08-04 — additional placement platforms (multi-platform posting); the main platform is still platform, and writing rules only follow it
    extra_platforms: list[str] = Field(default_factory=list, max_length=8)
    # Preset placement media — aligned with PublishPlanItem; 112b32b only added it to the output and missed it here,
    # causing PUT /execution-plan to 500 when _validate_plan_items read it.media
    media: Optional[str] = None
    rwj_resource_id: Optional[int] = None
    note: Optional[str] = None
    # 2026-05-28 — per-row combo override: empty array / omitted → use profile default
    # (profile.creation_directions × profile.copywriting_types).
    # Non-empty in a row → this row only runs this combo subset;
    # it never exceeds the GEO_CONTENT_MAX_COMBOS_PER_QUERY cap (the backend caps again).
    creation_directions: list[str] = Field(default_factory=list, max_length=10)
    copywriting_types: list[str] = Field(default_factory=list, max_length=10)


class ExecutionPlanUpdatePayload(BaseModel):
    items: list[PlanItemDraft]


class PlanGeneratePayload(BaseModel):
    """POST /execution-plan/generate-items — publishing table generation options.

    - source=seeds: approved seeds × rotating templates (default, same source as the initial plan)
    - source=strategy: schedule by rotating the strategy plan's keyword system (requires the plan to be generated)
    - per_day: how many articles per day
    - template_ids: content templates to use, rotated in the given order; empty = automatically pick system templates
    - platform_mode=sources: allocate placement platforms by domain share of measured cited sources (source_plan);
      platform_mode=strategy: first compute engine priority from the placement strategy (placement_strategy),
      allocating by the weighted citation distribution — strategy is upstream of scheduling, and its snapshot is persisted with the plan;
      platform_mode=template: use the template's own target_platforms (old behavior, default)
    - months: how many months to lay out at once (1 month = PLAN_WINDOW_DAYS days)
    """
    source: str = "seeds"
    per_day: int = Field(default=5, ge=1, le=10)
    template_ids: list[int] = Field(default_factory=list, max_length=10)
    platform_mode: str = "template"
    months: int = Field(default=1, ge=1, le=3)


class StartTopicPayload(BaseModel):
    """POST /admin/topics/{id}/start — launch config.

    All optional: launching without a body = old behavior (publishing table left empty, lazily generated at the default pace when entering the plan page).
    With a body, launch lays out the publishing table per config, so ops do not have to re-arrange it in the "generation options" dialog.

    - months: how many months to lay out at once (1 month = PLAN_WINDOW_DAYS days); on expiry it still renews automatically per the original logic
    - source / per_day / template_ids: same meaning as in PlanGeneratePayload
    - coverage_queries: coverage terms — pick a subset of the topic's prompts for this round's plan;
      empty = all prompts. Only affects publishing table allocation, not the topic's monitoring scope.
    """
    months: int = Field(default=1, ge=1, le=3)
    source: str = "seeds"
    per_day: int = Field(default=5, ge=1, le=10)
    template_ids: list[int] = Field(default_factory=list, max_length=10)
    coverage_queries: list[str] = Field(default_factory=list, max_length=1000)


class PlanAddItemPayload(BaseModel):
    """POST /execution-plan/items — add a plan row on a given date, optionally generating immediately.

    A shortcut for "schedule an article on a given day + pick template/creative parameters + produce the draft now",
    without first finding a slot in the editor and confirming. item.seq/id are set by the backend (appended to the end, displayed grouped by date).
    """
    item: PlanItemDraft
    generate_now: bool = True


class PlanBatchGenPayload(BaseModel):
    """POST /execution-plan/generate-docs — scope options for one-off batch article generation.

    The default pace is a daily cron producing drafts day by day; this gives ops an entry to "stockpile a batch of drafts first":
    - item_ids: name the plan row ids to generate directly; when given, only these are used and days/limit no longer apply
      (used by the calendar page "generate / regenerate this day now" — it must be able to pick any day, including past days,
       which the "next N days" window of days cannot express)
    - days: only generate plan rows with publish_date in [today, today+days); None = no date limit
    - limit: dispatch at most this many rows (first N by date+seq order); None = unlimited
    - include_existing: when True, rows that already have drafts are regenerated; by default only rows without articles are filled
    """
    item_ids: Optional[list[str]] = Field(default=None, max_length=200)
    days: Optional[int] = Field(default=None, ge=1, le=90)
    limit: Optional[int] = Field(default=None, ge=1, le=200)
    include_existing: bool = False


class PlanBatchGenOut(BaseModel):
    """generate-docs response: how many rows were dispatched (frontend notice + polling takes over progress)."""
    queued: int
    matched: int          # Rows matching the scope (including skipped ones that already have drafts)
    skipped_existing: int
    # Rows blocked because they are **already published** despite ticking "regenerate existing drafts" (the frontend must tell ops truthfully)
    skipped_published: int = 0


class TopicExecutionPlanOut(BaseModel):
    id: int
    topic_id: int
    generated_at: datetime
    generated_by_reviewer_id: Optional[int]
    status: str                          # draft / confirmed / failed
    error: Optional[str] = None
    # Overall project status
    overview: dict
    # Topic log (snapshot, append-only)
    topic_changelog: list[TopicChangelogEntry]
    # Expansion log (snapshot)
    expansion_log: list[ExpansionLogEntry]
    # Prompt list (snapshot, text array)
    monitored_queries: list[str]
    # 2026-08-14 — seed prompt → prompts under that seed (already intersected with monitored_queries).
    # The row editor uses it to fold and group term selection: expand a seed first, then tick from its expanded terms.
    # Empty-string key = legacy queries without a seed (grouped under "no seed").
    seed_queries: dict[str, list[str]] = Field(default_factory=dict)
    # Run progress — aggregated live; stops changing once the run completes
    run_id: Optional[int] = None
    run_status: Optional[str] = None     # running / success / failed
    progress: list[TopicProgressCell] = Field(default_factory=list)
    progress_done: int = 0
    progress_total: int = 0
    # Publishing plan — persisted publishing rows + live-aggregated coverage/article status
    publishing_plan: list[PublishPlanItem] = Field(default_factory=list)
    # Why the publishing table is still empty (only with a launch config):
    #   waiting_run — the baseline is still running; after it finishes, placement platforms are set from measured sources and the table is laid out
    #   no_seeds    — finished but no seeds / no templates, so no plan can be scheduled
    plan_pending_reason: Optional[str] = None
    # Editor metadata
    confirmed_at: Optional[datetime] = None
    confirmed_by_id: Optional[int] = None
    last_edited_at: Optional[datetime] = None


# ────────────── Content Template schemas ──────────────


class ContentTemplateOut(BaseModel):
    id: int
    scope: str                            # system / tenant / topic
    tenant_id: Optional[int]
    topic_id: Optional[int]
    name: str
    kind: str
    prompt_template: str
    target_platforms: list[str] = Field(default_factory=list)
    length_min: int
    length_max: int
    locked: bool
    locked_at: Optional[datetime]
    locked_by_id: Optional[int]
    created_by_id: Optional[int]
    created_at: datetime
    updated_at: datetime


class ContentTemplateCreate(BaseModel):
    scope: Literal["system", "tenant", "topic"] = "tenant"
    topic_id: Optional[int] = None
    name: str
    kind: str = "长文"
    prompt_template: str
    target_platforms: list[str] = Field(default_factory=list)
    length_min: int = 800
    length_max: int = 1500


class ContentTemplateUpdate(BaseModel):
    name: Optional[str] = None
    kind: Optional[str] = None
    prompt_template: Optional[str] = None
    target_platforms: Optional[list[str]] = None
    length_min: Optional[int] = None
    length_max: Optional[int] = None


def _parse_quality(raw: Optional[str]) -> Optional[dict]:
    """quality_json → dict; bad data does not block reading the draft."""
    if not raw:
        return None
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else None
    except Exception:  # noqa: BLE001
        return None


class GeneratedDocOut(BaseModel):
    id: int
    topic_id: int
    execution_plan_id: Optional[int]
    created_at: datetime
    # 2026-08-10 — last write time. Regeneration overwrites in place and created_at stays the same,
    # so only this reflects "when this body was written". Existing rows are backfilled with created_at.
    updated_at: Optional[datetime] = None
    source_query_text: str
    title: str
    body_markdown: str
    summary: str
    llm_model: str
    generation_error: Optional[str]
    # 2026-08-04 — failure requeue status (read-only; the frontend shows "retry 2/3 · next 14:20")
    gen_attempts: int = 0
    gen_next_retry_at: Optional[datetime] = None
    # 2026-08-04 — multi-agent refinement review results (read-only; the frontend shows quality score + uncovered questions + revisions)
    quality_score: Optional[int] = None
    quality: Optional[dict] = None
    status: str
    selected_for_review: bool
    review_decision_at: Optional[datetime]
    reviewer_id: Optional[int]
    reject_reason: Optional[str]
    publish_targets: list[dict]
    # v3.1 — 'ai' / 'user'
    source: str = "ai"
    # 2026-05-19 — URL-level ROI hits: {engine: [response_id]}
    cited_by: dict[str, list[int]] = Field(default_factory=dict)
    # 2026-05-29 — Mediumsly push status (read-only; written by the publisher, only displayed by the frontend)
    mediumsly_post_id: Optional[str] = None
    mediumsly_url: Optional[str] = None
    mediumsly_pushed_at: Optional[datetime] = None
    mediumsly_last_error: Optional[str] = None
    # 2026-05-28 — multi-variant generation identifiers (several drafts of the same query are distinguished by these two fields)
    creation_direction: Optional[str] = None
    copywriting_type: Optional[str] = None
    # 2026-07-28 — template used for generation (content_templates); template_name is injected by list_docs,
    # for the frontend "filter by template". Both None for user-supplied drafts / legacy without a template.
    template_id: Optional[int] = None
    template_name: Optional[str] = None
    template_kind: Optional[str] = None
    # 2026-06-12 — platform review result backfill (passed / rejected; rejected carries a reason for rule learning)
    platform_review_status: Optional[str] = None
    platform_reject_reason: Optional[str] = None
    # 2026-07-31 — link back to the publishing plan row: which plan row scheduled this draft.
    # plan_item_id is the row uuid (for machines); plan_seq / plan_publish_date are injected by the list endpoint
    # from the plan snapshot (for humans; the frontend shows "plan #N · date"). All three empty for user-supplied drafts.
    plan_item_id: Optional[str] = None
    plan_seq: Optional[int] = None
    plan_publish_date: Optional[str] = None
    # 2026-08-05 — article detail "relations" section: which seed prompt this draft was written for, which
    # prompts it should cover, which platform it goes to. All from the plan snapshot (injected via plan_link.plan_item_index);
    # empty for user-supplied drafts / legacy without a plan row.
    plan_seed: Optional[str] = None
    plan_queries: list[str] = Field(default_factory=list)
    plan_platform: Optional[str] = None
    # 2026-08-16 — content number (implicit label under the "Labeling Measures"). Shown in the explicit label bar at the top of the detail page,
    # linking to the provenance page. Empty for legacy / unlabeled drafts. The IP is not sent to the review page (only on the provenance page).
    gen_trace_no: Optional[str] = None

    @classmethod
    def from_orm_row(cls, r: "TopicGeneratedDocORM",
                     template_name: Optional[str] = None,
                     plan_row: Optional[dict] = None,
                     template_kind: Optional[str] = None) -> "GeneratedDocOut":
        try:
            targets = json.loads(r.publish_targets_json or "[]")
        except Exception:  # noqa: BLE001
            targets = []
        if not isinstance(targets, list):
            targets = []
        try:
            cited = json.loads(getattr(r, "cited_by_json", None) or "{}")
        except Exception:  # noqa: BLE001
            cited = {}
        if not isinstance(cited, dict):
            cited = {}
        return cls(
            id=r.id, topic_id=r.topic_id, execution_plan_id=r.execution_plan_id,
            created_at=r.created_at,
            updated_at=getattr(r, "updated_at", None) or r.created_at,
            source_query_text=r.source_query_text or "",
            title=r.title or "", body_markdown=r.body_markdown or "",
            summary=r.summary or "", llm_model=r.llm_model or "",
            generation_error=r.generation_error,
            gen_attempts=int(getattr(r, "gen_attempts", 0) or 0),
            gen_next_retry_at=getattr(r, "gen_next_retry_at", None),
            quality_score=getattr(r, "quality_score", None),
            quality=_parse_quality(getattr(r, "quality_json", None)),
            status=r.status, selected_for_review=bool(r.selected_for_review),
            review_decision_at=r.review_decision_at, reviewer_id=r.reviewer_id,
            reject_reason=r.reject_reason, publish_targets=targets,
            source=getattr(r, "source", None) or "ai",
            cited_by=cited,
            mediumsly_post_id=getattr(r, "mediumsly_post_id", None),
            mediumsly_url=getattr(r, "mediumsly_url", None),
            mediumsly_pushed_at=getattr(r, "mediumsly_pushed_at", None),
            mediumsly_last_error=getattr(r, "mediumsly_last_error", None),
            creation_direction=getattr(r, "creation_direction", None),
            platform_review_status=getattr(r, "platform_review_status", None),
            platform_reject_reason=getattr(r, "platform_reject_reason", None),
            copywriting_type=getattr(r, "copywriting_type", None),
            template_id=getattr(r, "template_id", None),
            template_name=template_name,
            template_kind=template_kind,
            plan_item_id=getattr(r, "plan_item_id", None),
            plan_seq=(plan_row or {}).get("seq"),
            plan_publish_date=(plan_row or {}).get("publish_date"),
            plan_seed=((plan_row or {}).get("seed") or None),
            plan_queries=list((plan_row or {}).get("queries") or []),
            plan_platform=(getattr(r, "platform", None)
                           or (plan_row or {}).get("platform") or None),
            gen_trace_no=getattr(r, "gen_trace_no", None),
        )


class PublishTargetItem(BaseModel):
    platform: str = Field(..., min_length=1, max_length=32)
    media: str = Field("", max_length=128)
    url: str = Field("", max_length=512)


class PublishPayload(BaseModel):
    # 2026-05-31 — min_length changed to 0: Mediumsly one-click publish takes the "no manual target, push_to_mediumsly=true"
    # path, and publish_targets is synthesized by the backend as needed after the publisher succeeds.
    publish_targets: list[PublishTargetItem] = Field(default_factory=list, max_length=20)
    push_to_mediumsly: bool = False


# v3.1 — users submit / edit articles they wrote themselves
class UserDocSubmitPayload(BaseModel):
    """User submits a draft they wrote — POST /api/content/topics/{topic_id}/docs."""
    title: str = Field(..., min_length=1, max_length=200)
    body_markdown: str = Field(..., min_length=1, max_length=100000)
    summary: str = Field("", max_length=500)
    # Associated prompt (optional, affects later effect attribution); empty = free-form draft
    source_query_text: str = Field("", max_length=1000)
    # "draft" = save only, stays on the user side; "pending_review" = goes straight into the admin queue
    submit_for_review: bool = False


class UserDocUpdatePayload(BaseModel):
    """User edits their own draft / rejected draft."""
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    body_markdown: Optional[str] = Field(None, min_length=1, max_length=100000)
    summary: Optional[str] = Field(None, max_length=500)
    source_query_text: Optional[str] = Field(None, max_length=1000)


# v3.1 — AI auto-generation schedule config
class AutoGenerateConfigPayload(BaseModel):
    enabled: bool
    # "HH:MM", 24h, Asia/Shanghai; the backend validates the format
    time: str = Field("09:00", min_length=4, max_length=5)
    count: int = Field(3, ge=1, le=20)
    # Auto-publish switch; None = unchanged this time, True/False = set. When admin turns it off, scheduled publishing stops.
    publish_enabled: Optional[bool] = None
    # Auto-approve drafts; None = unchanged. When off, generated drafts go to pending_review awaiting manual approval.
    approve_enabled: Optional[bool] = None
    # Paid media (Ruanwenjiang/Meijieku) auto-ordering on the scheduled date; None = unchanged.
    # ★ The only switch that spends money on its own, off by default; the UI must state this clearly before it is turned on.
    publish_paid_enabled: Optional[bool] = None
    # Time of day to publish date-only rows, "HH:MM"; None = unchanged.
    # Only covers plan rows without a time — rows written as YYYY-MM-DDTHH:MM follow their own time and are unaffected.
    publish_time: Optional[str] = Field(None, min_length=4, max_length=5)


class RejectDocPayload(BaseModel):
    reason: str = Field("", max_length=500)


class SelectDocsPayload(BaseModel):
    doc_ids: list[int] = Field(..., min_length=1, max_length=100)


class TopicMediaOut(BaseModel):
    """A topic's image / video assets — for the frontend GET /topics/{id}/media."""
    id: int
    topic_id: int
    filename: str
    kind: str           # "image" | "video"
    mime: str
    size: int
    # URL the frontend fetches the file from — aligned with the /blob route, storage_path not exposed
    url: str
    # Public URL (/public/topic-media/{tid}/{32hex}.{ext}, no Bearer needed) — what the editor's "insert image"
    # writes into the body. Without this field the frontend insert button stayed greyed out forever (fixed 2026-09-16)
    public_url: Optional[str] = None
    description: str = ""
    useCount: int = 0
    uploaded_at: datetime


class TopicPayload(BaseModel):
    enterprise_user_id: Optional[int] = Field(default=None, ge=1)
    name: str = Field(..., min_length=1, max_length=128)
    target: str = Field("", max_length=128)
    target_aliases: list[str] = Field(default_factory=list, max_length=10)
    industry: str = Field("", max_length=128)
    queries: list[str] = Field(..., min_length=1, max_length=1000)
    # Optional cluster_id array of the same length as queries; when lengths differ or omitted, all treated as 0
    query_cluster_ids: Optional[list[int]] = None
    clusters: Optional[list[ClusterMeta]] = None
    # 2026-05-20 — same length as queries: which seed prompt each query came from (for picker-side "group by seed").
    # Omitted / mismatched length → ignored by the backend, keeping the existing seed in queries_json (if any).
    query_seeds: Optional[list[str]] = None
    # 2026-06-11 — same length as queries: 4-dimensional scene labels (search/qa/intent/brand).
    # Omitted / mismatched length → ignored by the backend, keeping the existing scene_type in queries_json (if any).
    query_scene_types: Optional[list[str]] = None
    engines: list[str] = Field(..., min_length=1, max_length=10)
    # New topics do not enter periodic batch runs by default (see the comment on the ORM field enabled); old clients that omit it get off.
    enabled: bool = False
    # Phase C — on create / update, if the user filled seed prompts in the editor, submit them along;
    # the backend dedupes + appends to seed_prompts_json (status=pending), ensuring seed terms always enter the review flow.
    # The field name is distinct from TopicOut.seed_prompts (a list of SeedPromptItem) — this is only
    # submitted plain text; the backend adds status/timestamp when writing to the DB.
    seed_drafts: Optional[list[str]] = Field(default=None, max_length=10)
    # Phase D — brand profile (6 modules) submitted in the same request; the backend writes profile_json + appends a changelog.
    # None / missing = this save does not touch profile_json (backward compatible with old clients).
    profile: Optional[BrandProfile] = None
    # v1.4 — extra prompt configured by admin for the topic; appended to the end when assembling batch-run queries.
    # The regular user edit endpoint ignores this field (forcibly cleared at the API layer); only the admin workbench can write it.
    prompt_extension: Optional[str] = Field(default=None, max_length=2000)
    # Report center brand labels — submitted together from topic settings step 1 (report pages use them to match same-tier reference ranges).
    # None = this save does not touch the labels (backward compatible with old clients); "" = explicitly cleared.
    brand_tier: Optional[str] = Field(default=None, max_length=16)
    industry_class: Optional[str] = Field(default=None, max_length=16)


class TopicDraftPayload(BaseModel):
    """Step 1 persistence — POST /topics/draft. Stores only the brand profile (6 modules) + basic fields;
    queries/engines may be empty, submission_status=draft; later step 3 uses
    PUT /topics/{id} to fill queries/engines + submit for review."""
    enterprise_user_id: Optional[int] = Field(default=None, ge=1)
    name: str = Field("", max_length=128)
    target: str = Field("", max_length=128)
    target_aliases: list[str] = Field(default_factory=list, max_length=10)
    industry: str = Field("", max_length=128)
    engines: list[str] = Field(default_factory=list, max_length=10)
    enabled: bool = False
    profile: Optional[BrandProfile] = None
    # Report center brand labels — persisted when "next" is clicked in step 1, entering the draft together with the profile.
    brand_tier: Optional[str] = Field(default=None, max_length=16)
    industry_class: Optional[str] = Field(default=None, max_length=16)


class SeedPromptSubmitPayload(BaseModel):
    """Client adds new seed terms — POST /topics/{id}/seed-prompts."""
    text: str = Field(..., min_length=1, max_length=256)


class TopicOut(BaseModel):
    id: int
    # Data owner. The agency workbench uses it to label topics in one list with specific enterprises; not the operator ID.
    user_id: int
    name: str
    target: str
    target_aliases: list[str]
    industry: str
    queries: list[str]
    query_cluster_ids: list[int]
    # Phase C — status array of the same length as queries, for frontend badge rendering
    query_statuses: list[ReviewStatus]
    # Phase D — selected array of the same length as queries (flags for user-ticked prompts)
    query_selected: list[bool]
    # 2026-05-20 — seed term array of the same length as queries; "" for legacy / unrecorded positions
    query_seeds: list[str] = Field(default_factory=list)
    # 2026-05-26 — "original seed query" flags of the same length as queries. A query with is_seed=True is
    # a copy derived directly from an approved seed_prompt (query.text == seed.text), defaulting to selected=True
    # and locked=True (cannot be unticked in the UI). The frontend uses it for a badge + the "only show original seeds" filter.
    query_is_seed: list[bool] = Field(default_factory=list)
    # 2026-05-26 — locked flags of the same length as queries. When locked=True the frontend picker cannot untick it.
    # The only lock source currently: queries with is_seed=True (original seed queries are forced into monitoring).
    query_locked: list[bool] = Field(default_factory=list)
    # 2026-05-28 — scene labels of the same length as queries; "search" for legacy / unrecorded positions
    query_scene_types: list[SceneType] = Field(default_factory=list)
    # ★ 2026-08-17 — soft-delete flags of the same length as queries. **queries must keep returning all elements**
    # (including soft-deleted ones); the frontend's "include deleted" toggle and restore button rely on this array to tell them apart;
    # filtering here would misalign the parallel arrays, sticking term A's scene onto term B.
    query_deleted: list[bool] = Field(default_factory=list)
    clusters: list[ClusterMeta]
    seed_prompts: list[SeedPromptItem]
    engines: list[str]
    enabled: bool
    # Batch-run tier (configurable in the back office): tier is the config, effective_state is the **current actual state**
    # (daily/weekly/off/paused). The frontend only renders effective_state and does not derive it itself from
    # tier+enabled+paused — preventing another "invisible switch the back office cannot see".
    schedule_tier: str = "weekly"
    paused_until: Optional[datetime] = None
    effective_state: str = "weekly"
    resumes_at: Optional[datetime] = None
    # Cycle anchor (weekday 0=Mon..6=Sun / hour 0-23 local time); None = follow the global default
    cycle_anchor_weekday: Optional[int] = None
    cycle_anchor_hour: Optional[int] = None
    last_run_at: Optional[datetime]
    last_run_status: Optional[str]
    # Raw error text of the latest round. **Not a column**; filled by the API layer via a join looking up the latest run
    # (see api/ai_telemetry._last_run_errors) — so only the list / single-item endpoints include it,
    # and other callers of from_orm_row get None.
    #
    # Why it is needed: the workbench topic card used to print only the **raw DB value** of `last_run_status`
    # (running/success/failed), so a manual abort and a real crash looked exactly the same.
    # Yet the run table's error field spells it out (「aborted: 人工终止 —— 需 84900 积分,
    # 余额 21000」 ("manually aborted — needs 84900 credits, balance 21000")); it was just never sent out.
    last_run_error: Optional[str] = None
    # Phase D — profile application state machine
    profile: BrandProfile
    submission_status: str                  # draft / pending / approved / rejected
    submitted_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    rejected_at: Optional[datetime] = None
    reviewer_id: Optional[int] = None
    selected_query_count: int = 0           # Current number of queries with selected=True, for the frontend badge
    version: int = 1                        # Revision number: incremented on each _append_changelog, frontend badge "v3"
    # v3.1 — AI auto-generation schedule
    auto_generate_enabled: bool = False
    auto_generate_time: str = "09:00"
    auto_generate_count: int = 3
    auto_generate_last_run_at: Optional[datetime] = None
    auto_publish_enabled: bool = True       # Auto-publish by publish_date; admin can turn off
    auto_approve_enabled: bool = True       # Auto-approve drafts (off = goes to the manual review queue)
    auto_publish_paid_enabled: bool = False  # Paid media auto-ordering on the scheduled date (spends money, off by default)
    auto_publish_time: str = "11:00"        # Time of day for date-only rows; rows with a time follow their own
    # v1.4 — extra prompt configured by admin; TopicOut exposes it to the admin frontend, not rendered in the regular user frontend.
    prompt_extension: Optional[str] = None
    # Report center brand labels — brand_tier: ""/new/mid/leader; industry_class: ""/regular/special
    brand_tier: str = ""
    industry_class: str = ""
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm_row(cls, r: AiTelemetryTopicORM,
                     last_run_error: Optional[str] = None) -> "TopicOut":
        # queries_json supports 4 shapes:
        #   v0: list[str]
        #   v1: list[{text, created_at}]
        #   v2 (intent cluster): list[{text, created_at, cluster_id}]
        #   v3 (Phase C review): list[{text, cluster_id?, status, submitted_at?, ...}]
        #   v4 (Phase D monitoring selection): list[{text, ..., selected: bool}]
        raw = json.loads(r.queries_json or "[]")
        queries: list[str] = []
        cluster_ids: list[int] = []
        statuses: list[str] = []
        selected: list[bool] = []
        query_seeds: list[str] = []
        query_is_seed: list[bool] = []
        query_locked: list[bool] = []
        query_scene_types: list[str] = []
        query_deleted: list[bool] = []
        for q in raw:
            if isinstance(q, dict):
                t = q.get("text") or ""
                if t:
                    queries.append(t)
                    cid = q.get("cluster_id")
                    cluster_ids.append(int(cid) if isinstance(cid, int) else -1)
                    statuses.append(q.get("status") or "approved")
                    # Legacy without a selected field: default True (old topics' queries all participate in monitoring by default)
                    selected.append(bool(q.get("selected", True)))
                    query_seeds.append(str(q.get("seed") or ""))
                    query_is_seed.append(bool(q.get("is_seed", False)))
                    # locked defaults to follow is_seed — original seed queries are force-locked;
                    # an explicitly given locked field is respected
                    query_locked.append(bool(q.get("locked", q.get("is_seed", False))))
                    raw_scene = q.get("scene_type") or "search"
                    query_scene_types.append(raw_scene if raw_scene in ALL_SCENES else "search")
                    query_deleted.append(bool(q.get("deleted_at")))
            elif isinstance(q, str):
                queries.append(q)
                cluster_ids.append(-1)
                statuses.append("approved")
                selected.append(True)
                query_seeds.append("")
                query_is_seed.append(False)
                query_locked.append(False)
                query_scene_types.append("search")
                query_deleted.append(False)     # The legacy str form has no soft-delete concept
        clusters_raw = json.loads(r.clusters_json or "[]")
        clusters = [ClusterMeta(**c) for c in clusters_raw if isinstance(c, dict)]
        seeds_raw = json.loads(getattr(r, "seed_prompts_json", None) or "[]")
        seeds = [SeedPromptItem(**s) for s in seeds_raw if isinstance(s, dict)]
        try:
            profile_data = json.loads(getattr(r, "profile_json", None) or "{}")
        except Exception:  # noqa: BLE001
            profile_data = {}
        if not isinstance(profile_data, dict):
            profile_data = {}
        try:
            profile = BrandProfile(**profile_data)
        except Exception:  # noqa: BLE001
            profile = BrandProfile()
        # Scheduling state goes through the single deciding function; the frontend only renders effective_state without deriving it
        _sched_decision = _sched.resolve_schedule(
            getattr(r, "schedule_tier", None), bool(r.enabled),
            getattr(r, "paused_until", None),
        )
        return cls(
            id=r.id,
            user_id=r.user_id,
            name=r.name,
            target=r.target or "",
            target_aliases=json.loads(r.target_aliases_json or "[]"),
            industry=getattr(r, "industry", None) or "",
            queries=queries,
            query_cluster_ids=cluster_ids,
            query_statuses=statuses,
            query_selected=selected,
            query_seeds=query_seeds,
            query_is_seed=query_is_seed,
            query_locked=query_locked,
            query_scene_types=query_scene_types,
            query_deleted=query_deleted,
            clusters=clusters,
            seed_prompts=seeds,
            engines=json.loads(r.engines_json or "[]"),
            enabled=r.enabled,
            schedule_tier=_sched.normalize_tier(getattr(r, "schedule_tier", None)),
            paused_until=getattr(r, "paused_until", None),
            effective_state=_sched_decision.effective_state,
            resumes_at=_sched_decision.resumes_at,
            cycle_anchor_weekday=_sched.normalize_anchor_weekday(
                getattr(r, "cycle_anchor_weekday", None)),
            cycle_anchor_hour=_sched.normalize_anchor_hour(
                getattr(r, "cycle_anchor_hour", None)),
            last_run_at=r.last_run_at,
            last_run_status=r.last_run_status,
            last_run_error=last_run_error,
            profile=profile,
            submission_status=getattr(r, "submission_status", None) or "draft",
            submitted_at=getattr(r, "submitted_at", None),
            approved_at=getattr(r, "approved_at", None),
            rejected_at=getattr(r, "rejected_at", None),
            reviewer_id=getattr(r, "reviewer_id", None),
            # Soft-deleted terms are not in the badge count and do not take MAX_SELECTED_QUERIES slots
            selected_query_count=sum(1 for s, d in zip(selected, query_deleted) if s and not d),
            version=int(getattr(r, "version", 1) or 1),
            auto_generate_enabled=bool(getattr(r, "auto_generate_enabled", False) or False),
            auto_generate_time=str(getattr(r, "auto_generate_time", None) or "09:00"),
            auto_generate_count=int(getattr(r, "auto_generate_count", 3) or 3),
            auto_generate_last_run_at=getattr(r, "auto_generate_last_run_at", None),
            auto_publish_enabled=bool(getattr(r, "auto_publish_enabled", True)),
            auto_approve_enabled=bool(getattr(r, "auto_approve_enabled", True)),
            auto_publish_paid_enabled=bool(getattr(r, "auto_publish_paid_enabled", False)),
            auto_publish_time=str(getattr(r, "auto_publish_time", None) or "11:00"),
            prompt_extension=getattr(r, "prompt_extension", None),
            brand_tier=getattr(r, "brand_tier", None) or "",
            industry_class=getattr(r, "industry_class", None) or "",
            created_at=r.created_at,
            updated_at=r.updated_at,
        )


class RunNowCitation(BaseModel):
    url: str
    domain: str
    title: str = ""


class RunNowResult(BaseModel):
    engine: str
    query: str
    answer: str = ""
    citations: list[RunNowCitation] = Field(default_factory=list)
    error: Optional[str] = None


class RunOut(BaseModel):
    id: int
    topic_id: int
    status: str
    started_at: datetime
    finished_at: Optional[datetime]
    error: Optional[str]
    response_count: int = 0


class AdminRunOut(BaseModel):
    """Admin cross-user batch-run overview, one row = one run."""
    run_id: int
    topic_id: int
    topic_name: str
    topic_target: str
    user_id: int
    user_email: str
    started_at: datetime
    finished_at: Optional[datetime]
    status: str                      # running / success / failed
    error: Optional[str]
    response_count: int = 0          # Total responses of the run
    hit_count: int = 0               # Responses with hit=True
    error_count: int = 0             # Responses with a non-empty error

    @classmethod
    def from_orm_row(cls, r, response_count: int = 0) -> "RunOut":
        return cls(
            id=r.id, topic_id=r.topic_id, status=r.status,
            started_at=r.started_at, finished_at=r.finished_at,
            error=r.error, response_count=response_count,
        )


class ResponseOut(BaseModel):
    id: int
    engine: str
    query: str
    answer: str
    citations: list[RunNowCitation]
    video_url: Optional[str]
    source_url: Optional[str] = None
    error: Optional[str]
    created_at: datetime
    hit: bool = False
    hit_excerpt: Optional[str] = None
    mention_position: Optional[str] = None
    brand_rank: Optional[int] = None
    reasoning: Optional[str] = None       # Chain of thought, shown collapsed in the frontend, not mixed into answer

    # Raw exceptions (e.g. openrouter "402 Payment Required", internal URLs, model IDs) are never passed through to the frontend:
    # always converged into a user-friendly state, keeping the truth value (the frontend shows "failed" from it); details stay only in backend logs/DB.
    @field_validator("error", mode="before")
    @classmethod
    def _redact_error(cls, v):
        if not v:
            return None
        return "检测服务暂时不可用,系统将自动重试"


class KpiBlock(BaseModel):
    value: float
    delta_pct: Optional[float] = None    # Percent change vs previous period; None means not comparable
    sparkline: list[float] = Field(default_factory=list)  # Daily series within the period


class TrendPoint(BaseModel):
    date: str   # YYYY-MM-DD
    values: dict[str, int]   # engine -> citation count for that day


class DomainCount(BaseModel):
    domain: str
    count: int
    pct: float                    # 0-100, this domain's share of total citations


class OwnedSplit(BaseModel):
    owned: int                    # Citations matching our own keywords
    other: int                    # Citations not matching
    owned_pct: float              # 0-100
    delta_pct: Optional[float] = None   # vs previous period owned_pct


class ClusterBreakdownItem(BaseModel):
    cluster_id: int
    label: str
    query_count: int          # Queries in this cluster (topic level, not per period)
    response_count: int       # Successful responses this period (query × engine, errors excluded)
    mention_count: int        # Responses containing brand terms
    mention_rate: float       # mention_count / response_count(0 → 0.0)
    citation_count: int       # Cumulative response.citations


class IntentBreakdownOut(BaseModel):
    topic_id: int
    period_days: int
    brand_keywords: list[str]
    clusters: list[ClusterBreakdownItem]   # Sorted by size descending
    uncategorized: ClusterBreakdownItem    # Fallback bucket for unmatched cluster_id / old topics without clustering info


class OverviewOut(BaseModel):
    topic_id: int
    period_days: int
    brand_keywords: list[str]
    visibility: KpiBlock         # 0-100, the report center's "overall exposure rate" for the latest period (week); delta_pct in percentage points
    citations: KpiBlock          # Total citations
    growth: KpiBlock             # Citation growth rate (= citations.delta_pct, shown as its own card's main number)
    engines_covered: KpiBlock    # Engines with ≥1 successful response
    engines_total: int
    trend: list[TrendPoint]      # Citation counts bucketed by day, each day dict[engine]=count
    engines: list[str]           # Engines to draw on the trend chart (those appearing this period)
    top_domains: list[DomainCount]                  # Top 10 by citation count descending
    owned_split: OwnedSplit                         # Own vs other
    engine_domain_matrix: dict[str, dict[str, int]] # engine -> {domain: count}, only top_domains
    engine_factor_matrix: dict[str, dict[str, int]] = {}  # engine -> {GEO factor: count}, all citations this period


# ─────────────────── v1 citation tracking schemas ────────────────────


class QueryHitCell(BaseModel):
    """One cell in the matrix."""
    query: str
    engine: str
    status: str                          # pending/running/done
    first_hit_at: Optional[datetime]
    first_hit_response_id: Optional[int]
    last_checked_at: Optional[datetime]
    total_runs: int
    total_hits: int
    # 2026-05-26 — literal target / alias terms this (query, engine) cell has ever hit
    # (scan hit_excerpt for which ones it contains; the frontend uses it to support "filter by hit term")
    aliases_hit: list[str] = Field(default_factory=list)


class EngineFirstHit(BaseModel):
    """Timeline — the date an engine first hit (any query hit counts)."""
    engine: str
    first_hit_at: Optional[datetime]
    first_hit_query: Optional[str]
    days_after_start: Optional[int]     # = (first_hit_at - topic.created_at).days, empty = not hit yet


class TrackingMatrixOut(BaseModel):
    topic_id: int
    target: str
    target_aliases: list[str]
    queries: list[str]
    engines: list[str]
    started_at: datetime                # topic.created_at
    cells: list[QueryHitCell]
    timeline: list[EngineFirstHit]
    total_runs: int                     # Total historical runs of the topic
    total_cells: int                    # = len(queries) × len(engines)
    hit_cells: int                      # status=done and total_hits>=1
    hit_cells_pct: float                # = hit_cells / total_cells × 100


# ─────────────────── Growth report (first batch run vs current) ──────────────────


class GrowthQueryRow(BaseModel):
    """One prompt in the report — first batch-run result vs current cumulative result."""
    query: str
    seed: Optional[str] = None
    baseline_hit: bool
    baseline_rank: Optional[int]        # Best brand rank in the first batch run (when hit and a rank was extracted), otherwise None
    baseline_engines: list[str] = Field(default_factory=list)
    current_hit: bool
    current_rank: Optional[int]
    current_engines: list[str] = Field(default_factory=list)
    # new_hit (new hit) / improved (rank up) / steady (unchanged) / regressed (down) / still_miss (still not hit)
    status: str


class GrowthSnapshot(BaseModel):
    """Summary metrics of one snapshot (one for first, one for current)."""
    run_at: Optional[datetime]          # First = first run's started_at; current = generation time
    queries_total: int
    queries_hit: int
    hit_rate_pct: float                 # = queries_hit / queries_total × 100
    avg_rank: Optional[float]           # Average rank of questions that hit and have a rank
    engines_covered: int                # Engines with any hit


class GrowthReportOut(BaseModel):
    topic_id: int
    target: str
    generated_at: datetime
    has_baseline: bool                  # Whether a first batch run was found (False when no runs)
    baseline: GrowthSnapshot
    current: GrowthSnapshot
    rows: list[GrowthQueryRow]


# ─────────────────── v1 / v1.1 drawer details ──────────────────


class CellEvidence(BaseModel):
    """A "past answer" item shown in the drawer."""
    response_id: int
    run_id: int
    created_at: datetime
    engine: str
    query: str
    hit: bool
    hit_excerpt: Optional[str]
    source_url: Optional[str]
    answer: str
    citations: list[RunNowCitation]
    mention_position: Optional[str] = None
    reasoning: Optional[str] = None


class CellInsightRec(BaseModel):
    priority: str                       # P0/P1/P2
    title: str
    action: str
    why: str


class CompetitorMention(BaseModel):
    name: str
    count: int
    snippet: str


class CellInsightOut(BaseModel):
    id: int
    topic_id: int
    query: str
    engine: str
    window_start: datetime
    window_end: datetime
    verdict: str
    summary: str
    competitors_top3: list[CompetitorMention]
    recommendations: list[CellInsightRec]
    answer_format: Optional[str] = None
    citation_domains: list[str] = Field(default_factory=list)
    evidence_response_ids: list[int]
    llm_model: str
    prompt_version: str
    generated_at: datetime
    feedback: Optional[str]


class CellDrawerOut(BaseModel):
    """Full drawer content — cell status + past answers + LLM diagnosis (if any)."""
    cell: QueryHitCell
    evidence: list[CellEvidence]
    insight: Optional[CellInsightOut]    # None = not generated yet (only after the user clicks "Analyze")


# ─────────────────── v1.2 weekly report ──────────────────


class BriefingAction(BaseModel):
    priority: str                       # P0/P1/P2
    title: str
    why: str
    how: str


class BriefingOut(BaseModel):
    id: int
    topic_id: int
    period_start: datetime
    period_end: datetime
    period_type: str = "weekly"         # weekly | daily
    body_md: str
    kpi_snapshot: dict
    top_actions: list[BriefingAction]
    delivered_email_at: Optional[datetime]
    feedback_score: Optional[int]
    llm_model: str
    prompt_version: str
    generated_at: datetime


class FeedbackPayload(BaseModel):
    feedback: str = Field(..., pattern="^(helpful|not_helpful|wrong)$")


class BriefingFeedbackPayload(BaseModel):
    score: int = Field(..., ge=1, le=5)


# ─────────────────── v1.3 SAIV / competitor share ──────────────────────


class CompetitorShareEntry(BaseModel):
    name: str
    count: int
    pct: float                          # This competitor / whole industry (brand + all competitors) mention count × 100


class PositionDist(BaseModel):
    """Hit position distribution — simplified search ranking."""
    lead: int = 0
    body: int = 0
    tail: int = 0
    unknown: int = 0


class PositionBreakdown(BaseModel):
    """Metric definitions for the brand growth radar + core metric cards.

    ★ 2026-08 revision: all six percentages come from the **report center** data layer
    (geo.services.report_metrics_source.latest_period), no longer aggregated locally —
    previously this was "cell-level × full lifetime × no term-type filter", and none of the numbers matched the report center pages.
    Definitions and denominators in ai_telemetry_reports._metrics_from_counts; window = latest period (weekly by default).
    Field names are kept to avoid changing frontend types and existing industry_baseline data; semantics in each line's comment.
    """
    top1_pct: float = 0.0           # TOP1 exposure rate
    top3_pct: float = 0.0           # TOP3 exposure rate
    visible_pct: float = 0.0        # Overall exposure rate
    source_pct: float = 0.0         # Article citation rate (the former "source share" could exceed 100%, fixed with the unified definition)
    accuracy_pct: float = 0.0       # Content neutral-positive rate
    mention_pct: float = 0.0        # Brand share of voice
    seed_coverage_pct: float = 0.0  # Seed coverage — unique to brand growth, not in the report center;
                                    # seeds with at least one expanded query ever hit / total approved seeds × 100


class EngineSlice(BaseModel):
    """Single-engine slice of an engine within a group (denominator = queries in the group × 1)."""
    engine: str
    breakdown: PositionBreakdown
    total_queries: int


class GroupBreakdown(BaseModel):
    """Metric summary of a set of queries + per-engine slices (left for future extension; currently only the query group)."""
    scope: Literal["query"]
    total_queries: int                       # N
    total_engines: int                       # M
    total_cells: int                         # = N × M
    breakdown: PositionBreakdown             # Summary across all engines
    by_engine: list[EngineSlice] = Field(default_factory=list)


class PositionBreakdownOut(BaseModel):
    topic_id: int
    period_days: int
    industry: str
    # Compatibility field — same value as query_group; old consumers like RadarBlock keep reading here
    total_cells: int
    total_queries: int
    breakdown: PositionBreakdown
    industry_baseline: Optional[PositionBreakdown] = None   # NULL when the sample is insufficient; the frontend does not render it
    # New: the whole query group + per-engine slices (for multi-model comparison)
    query_group: Optional[GroupBreakdown] = None
    engines: list[str] = Field(default_factory=list)        # Order of topic.engines_json


class IndustryBenchmarkOut(BaseModel):
    industry: str
    sample_size: int                # Number of topics aggregated
    breakdown: Optional[PositionBreakdown] = None    # NULL when the sample is <3


class CompetitorSubstitutionItem(BaseModel):
    """C3 competitor analysis "displacement evidence" — queries that mentioned a competitor but not us."""
    query: str
    competitor_name: str
    competitor_count: int        # Total times this competitor was mentioned across this query's past answers
    sample_response_id: int      # For jumping to concrete evidence
    sample_snippet: str          # Short excerpt


class CompetitorSubstitutionOut(BaseModel):
    topic_id: int
    period_days: int
    competitor_filter: Optional[str]   # Single-competitor filter; None = all
    items: list[CompetitorSubstitutionItem]   # Sorted by competitor_count descending
    total: int


class ShareOfVoiceOut(BaseModel):
    """Share of voice — brand vs competitor AI mention counts within the recent window.

    SAIV(Share of AI Voice) = brand_count / (brand_count + sum(competitors.count)) × 100.
    One ResponseORM: hit=True contributes brand_count + 1;
                     each {name, count} in competitors_json contributes to that name's total.
    """
    topic_id: int
    target: str
    period_days: int                    # Statistics window
    brand_count: int                    # Times the brand is mentioned across all Response answers
    competitors_count_total: int        # Total mentions of all competitors
    saiv_pct: float                     # 0-100, brand share
    competitors: list[CompetitorShareEntry]   # Top 10 by count descending
    position_dist: PositionDist         # Hit position distribution (lead/body/tail/unknown)
    optimal_rate_pct: float             # AI answer preference rate = sum(total_hits) / sum(total_runs) × 100
    total_runs: int                     # Cumulative runs of the topic
    sample_size: int                    # Response rows used for aggregation (including hit=False)


# ─────────────── v3.3 — GEO brand growth strategy plan ──────────────


class SolutionDiagnosisCheck(BaseModel):
    """Compact snapshot of a single 25-category check from the diagnosis (for the strategy plan, without details like i18n keys)."""
    category: str           # Check category (English label, e.g. "Structured Data")
    status: str             # PASS / WARN / FAIL / INFO
    message: str            # Human-readable message
    fix: Optional[str] = None


class SolutionDiagnosisCluster(BaseModel):
    """5 major weakness clusters — the 25 categories grouped into themes (AI protocols / crawler performance / structured data / URL & meta tags / security and content).

    `severity` is for frontend sorting + red highlighting: high = at least one FAIL, med = WARN but no FAIL, low = all PASS/INFO.
    """
    key: str                # cluster slug,e.g. "ai_protocol"
    title_zh: str           # Chinese cluster title
    severity: str           # high / med / low
    summary: str            # LLM-written weakness narrative for the cluster (40-120 characters)
    bullets: list[str] = Field(default_factory=list)   # Specific weakness items (each ≤ 60 characters)
    checks: list[SolutionDiagnosisCheck] = Field(default_factory=list)  # Specific checks in the cluster


class SolutionDiagnosis(BaseModel):
    """Full diagnosis block — geo_checker results + 5 clusters after LLM clustering + total score."""
    url: str
    score: int              # 0-100
    grade: str              # A+/A/B/C/D/F
    pass_count: int = 0
    warn_count: int = 0
    fail_count: int = 0
    info_count: int = 0
    clusters: list[SolutionDiagnosisCluster] = Field(default_factory=list)
    # Three-layer execution flow (technical foundation / content core / authority boost) — derived automatically from cluster severity
    execution_layers: list[dict] = Field(default_factory=list)
    # Raw 25 categories (unclustered); the frontend can expand them for details
    all_checks: list[SolutionDiagnosisCheck] = Field(default_factory=list)


class SolutionSevenStepItem(BaseModel):
    """A single step of the seven-step growth model — the LLM customizes `core_action` / `output_value` for the brand."""
    step: int               # 1-7
    name: str               # Chinese step name (e.g. "种子提示词搭建" ("seed prompt setup"))
    core_goal: str          # Core goal (short sentence)
    core_action: str        # Execution content for this brand (from the LLM)
    output_value: str       # Core output & value


class SolutionKeywordTier(BaseModel):
    """Keyword layers — one layer = one type (brand core / product / technology / scenario / purchase decision / pain point)."""
    tier: str               # tier slug,e.g. "brand_core"
    title_zh: str           # Chinese layer name
    description: str        # Positioning of this layer (one sentence from the LLM)
    keywords: list[str] = Field(default_factory=list)   # Specific terms


class SolutionVisionItem(BaseModel):
    """Expected vision — three sections: industry authority / lower acquisition cost / global trust, customized by the LLM for the brand."""
    title: str              # Subtitle
    body: str               # One paragraph (60-180 characters)


class SolutionQueryItem(BaseModel):
    """A single monitored query snapshot item — extracted directly from the selected=True items in topic.queries_json."""
    text: str
    cluster_id: int = -1    # -1 means ungrouped
    seed: str = ""          # Which seed prompt this query was expanded from (empty string = legacy)


class SolutionQueryCluster(BaseModel):
    """Monitored query cluster label — taken directly from topic.clusters_json, used by the frontend to group by cluster_id."""
    cluster_id: int
    label: str


class SolutionQueriesSnapshot(BaseModel):
    """Monitored query list snapshot — locks in the user-ticked (selected=True) queries at report generation.

    No LLM: the data comes from the user's own ticks in the "prompts" step + K-Means clustering results.
    """
    clusters: list[SolutionQueryCluster] = Field(default_factory=list)
    queries: list[SolutionQueryItem] = Field(default_factory=list)


class TopicSolutionOut(BaseModel):
    """Full strategy plan — response of GET /topic/{id}/solution."""
    id: int
    topic_id: int
    status: str                                      # idle / generating / ready / failed
    website_url: str
    error: Optional[str] = None
    generated_by_admin_id: Optional[int] = None
    llm_model: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # Content blocks — fields may be empty when status != ready
    brand_snapshot: Optional[BrandProfile] = None
    diagnosis: Optional[SolutionDiagnosis] = None
    seven_steps: list[SolutionSevenStepItem] = Field(default_factory=list)
    keyword_tiers: list[SolutionKeywordTier] = Field(default_factory=list)
    vision: list[SolutionVisionItem] = Field(default_factory=list)
    queries_snapshot: Optional[SolutionQueriesSnapshot] = None


class GenerateSolutionPayload(BaseModel):
    """POST /topic/{id}/solution/generate — triggered by admin.

    website_url is optional: if omitted, the 25-category on-site technical diagnosis is skipped and only 4 blocks are generated
    (brand positioning / seven-step model / keywords / vision).
    """
    website_url: str = Field("", max_length=512)
