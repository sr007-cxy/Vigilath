"""Phase D -- content copy generation service.

Entry point `schedule_generation(topic_id, plan_id)` is triggered asynchronously via FastAPI BackgroundTasks by
admin_review.approve_topic when an admin approves the materials.

Flow:
  1. Load the topic's materials + approved prompts (approved and selected)
  2. For each prompt, assemble the LLM prompt (content direction/copy type/platform/tone/no-go areas/slogan from the materials)
  3. Drafting goes through `_compose()` -- by default the multi-agent refinement pipeline (services/content_quality.py:
     plan -> write -> review -> revise); with GEO_CONTENT_QUALITY=0 it falls back to single-shot generation
  4. Persist TopicGeneratedDocORM (review score into quality_score/quality_json; status per the review switch below:
     review off (default) successful drafts go straight to approved; review on goes to pending_review)

Retry on failure: generation failures go through services/gen_retry.mark_failure -- retryable causes (timeout/429/5xx)
schedule the next automatic retry; terminal causes (402/401/output truncation) are recorded without queueing.

Model is DeepSeek-Chat. Two paths:
  - Direct: set DEEPSEEK_API_KEY, uses https://api.deepseek.com/chat/completions
  - Via OpenRouter: set OPENROUTER_API_KEY, model deepseek/deepseek-chat
Neither set -> an error is recorded on persist, without blocking the review flow.

Failure granularity: a single query failure does not affect others; failed drafts record the reason in generation_error.

ENV:
    DEEPSEEK_API_KEY      optional, preferred -> direct DeepSeek
    OPENROUTER_API_KEY    optional, fallback -> call the DeepSeek model via OpenRouter
    DEEPSEEK_BASE_URL     optional, default https://api.deepseek.com
    DEEPSEEK_MODEL        direct model id, optional, default deepseek-chat
    OPENROUTER_DEEPSEEK_MODEL optional, default deepseek/deepseek-chat
    GEO_CONTENT_TIMEOUT   optional, per-request LLM timeout (seconds), default 180
    GEO_CONTENT_MAX_DOCS  optional, max drafts per review (default = number of prompts, cap 50)
    GEO_CONTENT_MAX_TOKENS      optional, per-call output cap, default 16000 (unset gets truncated at the model default 4096)
    GEO_CONTENT_MAX_TOKENS_RETRY optional, budget for the escalated retry after truncation, default 32000
    GEO_CONTENT_HTTP_RETRIES    optional, immediate retries on transient errors, default 2
    GEO_CONTENT_HTTP_BACKOFF    optional, retry backoff base (seconds), default 3
    GEO_CONTENT_QUALITY / _MIN_SCORE / _REVISIONS / _OUTLINE
                          multi-agent refinement pipeline switch and params, see services/content_quality.py
    GEO_IMAGE_CLAIM_TTL_SEC     optional, auto-image lease seconds, default 7200
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import requests
from sqlalchemy import func, or_

from geo.database import SessionLocal
from geo.models.ai_telemetry import (
    AiTelemetryTopicExecutionPlanORM, AiTelemetryTopicORM, BrandProfile,
    ContentTemplateORM, TopicGeneratedDocORM, TopicMediaORM, featured_products,
)
from geo.models.llm_usage import (
    STATUS_HTTP_ERROR, STATUS_NETWORK_ERROR, STATUS_OK, STATUS_PARSE_ERROR, STATUS_TRUNCATED,
)
from geo.models.balance import (
    KIND_CONTENT_DOC, KIND_CONTENT_IMAGE,
    PRICE_CONTENT_DOC, PRICE_CONTENT_IMAGE,
)
from geo.services import content_quality, gen_batch, gen_retry, topic_billing
from geo.services.llm_usage import record_llm_call, spawn_thread, usage_ctx

log = logging.getLogger(__name__)


# -- Manual review switch for generated drafts (off by default since 2026-08-05) ----------------------------
# Review is turned off for now: successfully generated drafts go straight to approved and can immediately be "selected to publish" / sent to channels,
# without queueing for a manual approval. Failed / not-yet-generated placeholder drafts stay in pending_review (with
# generation_error, queued for retry) and are never released as publishable content.
# To restore manual review: set env var GEO_CONTENT_REVIEW_ENABLED=1.
def review_enabled() -> bool:
    return (os.environ.get("GEO_CONTENT_REVIEW_ENABLED", "0").strip().lower()
            in ("1", "true", "yes", "on"))


def _ok_status(db=None, topic_id: Optional[int] = None) -> str:
    """Landing status after successful generation: auto-approve = straight to approved, manual review = into the pending_review queue.

    Since 2026-09-10 **decided per topic**: topic.auto_approve_enabled defaults to True, exactly matching the previous global default
    (GEO_CONTENT_REVIEW_ENABLED unset = review off = straight to approved), so adding this column
    changes nothing for existing topics; to restore manual review, turn it off for that one topic.
    When no topic is available (old call sites / synthetic data) fall back to the global env, same behavior as before the change.
    """
    if db is not None and topic_id:
        from geo.models.ai_telemetry import AiTelemetryTopicORM
        t = db.get(AiTelemetryTopicORM, topic_id)
        if t is not None:
            return "approved" if bool(getattr(t, "auto_approve_enabled", True)) \
                else "pending_review"
    return "pending_review" if review_enabled() else "approved"


DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
OPENROUTER_DEEPSEEK_MODEL = os.environ.get("OPENROUTER_DEEPSEEK_MODEL", "deepseek/deepseek-chat")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_TIMEOUT = int(os.environ.get("GEO_CONTENT_TIMEOUT", "180"))
# 2026-08-04 -- explicitly give enough output budget. deepseek-chat defaults max_tokens to only 4096, while long_form
# often reaches 3000+ chars (~3500 tokens); with JSON escaping on top it hits the cap -> output truncated ->
# JSON cannot close -> "failed to parse JSON from LLM output". Retrying does not fix this kind of failure;
# it must be fixed at the output budget.
# 2026-08-21 -- raised to 24000: 8000 was set back when deepseek-chat capped at 8192, but the model has long since
# switched to **deepseek-v4-flash (a reasoning model)** on the neolink gateway -- chain of thought also eats this budget;
# one measured long draft had completion=15261 with reasoning taking 12742, leaving only 739 headroom under 16000.
# A sufficient budget costs nothing extra (billed by actual output); an insufficient one fails outright with finish_reason=length,
# while the plan row still shows "draft produced". If it is hit, MAX_TOKENS_RETRY below escalates as a fallback.
MAX_TOKENS = int(os.environ.get("GEO_CONTENT_MAX_TOKENS", "24000"))
# 2026-08-21 -- after truncation, automatically retry once with more budget. Ranking templates (ranking roundup / decision ranking + long_form)
# output a dozen-plus institutions x multiple dimensions of entries and tables, plus JSON escaping, and get truncated even at 16000; ordinary long articles
# need only 3500 tokens, so raising the default all the way just wastes budget on every request. So escalate on demand:
# only rerun with the big budget when finish_reason=length is actually hit (32000 measured to work).
MAX_TOKENS_RETRY = int(os.environ.get("GEO_CONTENT_MAX_TOKENS_RETRY", "32000"))
# Back off and retry transient errors (timeout / 429 / 5xx) on the spot -- most are absorbed here, no need to wait for the next retry queue round.
HTTP_RETRIES = int(os.environ.get("GEO_CONTENT_HTTP_RETRIES", "2"))
HTTP_BACKOFF_SEC = float(os.environ.get("GEO_CONTENT_HTTP_BACKOFF", "3"))
# HTTP statuses worth retrying on the spot: rate limiting + upstream jitter. 402/401/403 retries are useless; raise directly.
_RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 522, 524}


def schedule_generation(
    *,
    topic_id: int,
    plan_id: int | None = None,
    max_docs: int | None = None,
    queries_override: list[str] | None = None,
    plan_item_ids: list[str] | None = None,
    mark_auto_run: bool = False,
    vscl_action_id: int | None = None,
    trigger_source: str = "unknown",
    operation_context_id: int | None = None,
) -> None:
    """fire-and-forget thread. BackgroundTasks is already fire-and-forget,
    but to avoid blocking FastAPI's event loop (30-90s per LLM call), start another daemon thread.

    Parameters:
      max_docs            -- limit the number of drafts this run; if omitted, fall back to env / 50
      queries_override    -- the list of queries to write this run; if omitted, pick the best of plan_item_ids /
                            plan.publishing_plan_json / topic.queries_json
      plan_item_ids       -- only run these rows of publishing_plan_items (for single-item regeneration)
      mark_auto_run       -- when True, write auto_generate_last_run_at (for cron / generate-now entry points)
      vscl_action_id      -- when triggered by Vigilath-Siyuan siyuan_tick, bind this batch of drafts to that VSCL-RL
                            action (persisted to topic_generated_docs.vscl_action_id), so reward settlement /
                            citation attribution can trace it reliably. Applies to both old and new generation paths.
      trigger_source      -- who triggered this run (plan_confirm / regenerate_item / run_now /
                            cron / siyuan / agent ...), recorded in the publish batch ledger. Defaults to unknown
    """
    # Use spawn_thread instead of a bare threading.Thread: bare threads do not inherit contextvars,
    # so the accounting context (topic_id / trace_id / feature) would break here and everything would be persisted as unknown.
    thread = spawn_thread(
        _run_generation_safe,
        topic_id, plan_id, max_docs, queries_override, plan_item_ids,
        mark_auto_run, vscl_action_id, trigger_source, operation_context_id,
    )
    thread.start()


def _run_generation_safe(
    topic_id: int, plan_id: int | None,
    max_docs: int | None, queries_override: list[str] | None,
    plan_item_ids: list[str] | None,
    mark_auto_run: bool,
    vscl_action_id: int | None = None,
    trigger_source: str = "unknown",
    operation_context_id: int | None = None,
) -> None:
    # Accounting context: all model calls in this batch share one trace_id, so the page can drill down by "one generation batch".
    # The publish batch ledger reuses this trace_id directly as the batch id (see TopicContentGenBatchORM),
    # so "how much this batch cost and how many model calls it made" can be joined directly via llm_calls.trace_id.
    with usage_ctx(feature="content_gen", topic_id=topic_id, trace_id=uuid.uuid4().hex,
                   ref_type="plan" if plan_id else "topic",
                   ref_id=str(plan_id or topic_id)):
        try:
            _run_generation(
                topic_id, plan_id, max_docs, queries_override,
                plan_item_ids, mark_auto_run, vscl_action_id, trigger_source,
                operation_context_id,
            )
        except Exception as e:  # noqa: BLE001
            log.exception("content generation crashed for topic %d: %s", topic_id, e)


def _resolve_provider() -> tuple[str | None, str, str]:
    """Pick the LLM provider + get the api key. Returns (provider | None, model_id, api_key)."""
    ds_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    or_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if ds_key:
        return "deepseek", DEEPSEEK_MODEL, ds_key
    if or_key:
        return "openrouter", OPENROUTER_DEEPSEEK_MODEL, or_key
    return None, DEEPSEEK_MODEL, ""


def _run_generation(
    topic_id: int, plan_id: int | None,
    max_docs_override: int | None, queries_override: list[str] | None,
    plan_item_ids: list[str] | None,
    mark_auto_run: bool,
    vscl_action_id: int | None = None,
    trigger_source: str = "unknown",
    operation_context_id: int | None = None,
) -> None:
    db = SessionLocal()
    try:
        t = db.get(AiTelemetryTopicORM, topic_id)
        if not t:
            log.warning("content gen: topic %d not found", topic_id)
            return
        if operation_context_id is not None:
            from geo.models.organization import OperationContextORM
            from geo.services.access_control import revalidate_operation_context
            from geo.services.system_audit import append_event

            operation = db.get(OperationContextORM, operation_context_id)
            try:
                revalidate_operation_context(db, operation_context_id)
            except Exception as exc:  # noqa: BLE001
                batch = gen_batch.open_batch(
                    db,
                    topic_id,
                    trigger_source=trigger_source,
                    plan_id=plan_id,
                    operation_context_id=operation_context_id,
                )
                if batch is not None:
                    batch.finished_at = datetime.utcnow()
                    batch.note = "cancelled: authorization revoked"
                append_event(
                    db,
                    operation_context=operation,
                    event_type="authorization_denied",
                    action="content.generate.start",
                    object_type="content_gen_batch",
                    object_id=(batch.id if batch is not None else None),
                    result="denied",
                    reason=str(exc),
                )
                db.commit()
                return
        try:
            profile_data = json.loads(t.profile_json or "{}")
        except Exception:  # noqa: BLE001
            profile_data = {}
        if not isinstance(profile_data, dict):
            profile_data = {}
        try:
            profile = BrandProfile(**profile_data)
        except Exception:  # noqa: BLE001
            profile = BrandProfile()

        # New path first: plan_id + plan.publishing_plan_json has content -> dispatch by plan item.
        # The old path (queries_override / topic.queries_json) only runs when there is no plan data.
        plan_items: list[dict] = []
        if plan_id is not None:
            plan = db.get(AiTelemetryTopicExecutionPlanORM, plan_id)
            if plan:
                try:
                    raw = json.loads(plan.publishing_plan_json or "[]")
                except Exception:  # noqa: BLE001
                    raw = []
                plan_items = [it for it in raw if isinstance(it, dict)]
                if plan_item_ids:
                    wanted = set(plan_item_ids)
                    plan_items = [it for it in plan_items if it.get("id") in wanted]

        if plan_items:
            if not _afford_or_stop(db, t, len(plan_items), trigger_source,
                                   plan_item_ids=[it.get("id") for it in plan_items
                                                  if it.get("id")]):
                return
            batch = gen_batch.open_batch(db, topic_id, trigger_source=trigger_source,
                                         plan_id=plan_id,
                                         operation_context_id=operation_context_id)
            _run_per_item(
                db, t, profile, topic_id, plan_id, plan_items, mark_auto_run,
                vscl_action_id=vscl_action_id, batch=batch,
            )
            return

        # -- Legacy path compatibility: run the whole batch of queries ------------------------------------------
        if queries_override is not None:
            queries = [q for q in queries_override if isinstance(q, str) and q.strip()]
        else:
            try:
                qarr = json.loads(t.queries_json or "[]")
            except Exception:  # noqa: BLE001
                qarr = []
            queries = [
                q["text"] for q in qarr
                if isinstance(q, dict) and q.get("text")
                and q.get("selected", True) and q.get("status") == "approved"
                and not q.get("deleted_at")            # Soft-deleted terms are excluded from publish term selection
            ]
        if not queries:
            log.info("content gen: no queries to write for topic %d", topic_id)
            return
        env_cap = int(os.environ.get("GEO_CONTENT_MAX_DOCS", "50"))
        cap = max_docs_override if max_docs_override and max_docs_override > 0 else env_cap
        cap = min(cap, env_cap, len(queries))
        queries = queries[:cap]

        provider, model_id, api_key = _resolve_provider()
        style_refs = _load_style_refs(db, topic_id)
        cite_guidelines = _load_citation_playbook(db, topic_id)
        query_gap_map = _load_query_gap_map(db, topic_id)

        # 2026-05-28 -- second wave of 4-dimension scenario expansion: generate multiple variants per (creation_direction, copywriting_type)
        # combo. Both filled in the profile -> NxM combos; one side empty -> single-side fan-out;
        # both empty -> fallback single variant (backward compatible).
        combos = _build_combos(profile)
        log.info("content gen: topic %d, %d queries × %d combos = %d docs target",
                 topic_id, len(queries), len(combos), len(queries) * len(combos))

        img_budget: dict = {}
        new_docs: list[TopicGeneratedDocORM] = []
        if not _afford_or_stop(db, t, len(queries) * len(combos), trigger_source):
            return
        batch = gen_batch.open_batch(db, topic_id, trigger_source=trigger_source,
                                     plan_id=plan_id,
                                     operation_context_id=operation_context_id)
        db.commit()  # Persist the batch first; each article then commits independently so image counts stay in the same transaction as article success
        for q in queries:
            for direction, copywriting_type in combos:
                doc = TopicGeneratedDocORM(
                    topic_id=topic_id, execution_plan_id=plan_id,
                    source_query_text=q, status="pending_review",
                    selected_for_review=True,
                    llm_model=model_id, source="ai",
                    creation_direction=direction,
                    copywriting_type=copywriting_type,
                    vscl_action_id=vscl_action_id,
                )
                doc._gen_queries = [q]         # Term snapshot for this article (old path: one article per term)
                if not provider:
                    doc.title = f"[未生成] {q}"
                    db.add(doc)
                    gen_retry.mark_failure(
                        db, doc, "DEEPSEEK_API_KEY / OPENROUTER_API_KEY 都未配置")
                    db.flush()
                    gen_batch.stamp_docs(batch, [doc])
                    db.commit()
                    new_docs.append(doc)
                    continue
                selection = None
                old_orphan_ids: list[int] = []
                try:
                    title, body, summary, quality = _compose(
                        profile=profile, topic_text=q, provider=provider, api_key=api_key,
                        direction=direction, copywriting_type=copywriting_type,
                        topic_id=topic_id, style_refs=style_refs,
                        cite_guidelines=_merge_query_guidelines(
                            cite_guidelines, query_gap_map, [q]),
                        material_refs=_load_material_refs(db, t, topic_id, q),
                        cover_queries=[q],
                    )
                    selection, medias = _prepare_article_image(
                        db, t, profile, img_budget,
                        title=title, summary=summary, body=body, query=q)
                    _apply_quality(doc, quality)
                    doc.title = title
                    doc.body_markdown = _embed_media_images(body, medias)
                    doc.summary = summary
                    doc.status = _ok_status(db, topic_id)
                    db.add(doc)
                    db.flush()
                    gen_batch.stamp_docs(batch, [doc])
                    old_orphan_ids = _finalize_article_image(db, t, doc, selection)
                    db.commit()
                except Exception as e:  # noqa: BLE001
                    db.rollback()
                    _abort_article_image(selection)
                    log.warning("content gen failed for q='%s' combo=(%s,%s) topic=%d: %s",
                                q, direction, copywriting_type, topic_id, e)
                    doc.title = f"[生成失败] {q}"
                    # Failures go into the retry queue (only retryable causes are queued; terminal causes are recorded without queueing)
                    gen_retry.mark_failure(db, doc, str(e))
                    db.add(doc)
                    db.flush()
                    gen_batch.stamp_docs(batch, [doc])
                    db.commit()
                else:
                    from geo.services.image_orphan_cleanup import cleanup_orphan_media_ids
                    cleanup_orphan_media_ids(old_orphan_ids)
                new_docs.append(doc)
        if mark_auto_run:
            t.auto_generate_last_run_at = datetime.utcnow()
        gen_batch.close_batch(db, batch, new_docs, queries=queries,
                              stats={"created": len(new_docs), "regenerated": 0})
        db.commit()
        # The body is no longer labeled (2026-08-17); only the three provenance columns are written here; still ordered before the compliance scan --
        # if the lexicon still has the mandatory "内容由AI生成" ("AI-generated content") disclaimer enabled, the whole batch would be marked returned by our own gate,
        # so that item must be disabled in the compliance lexicon admin
        _stamp_marks(db, new_docs)
        _compliance_scan_batch(db, t, new_docs)
        log.info("content gen done for topic %d: %d queries × %d combos",
                 topic_id, len(queries), len(combos))
    finally:
        db.close()


# 2026-05-28 -- combo construction: take creation_directions x copywriting_types from BrandProfile.
# One side empty -> use a None placeholder (degrades in the prompt, no hint injected); both empty -> single (None, None) -> old path
_MAX_COMBOS_PER_QUERY = int(os.environ.get("GEO_CONTENT_MAX_COMBOS_PER_QUERY", "9"))



def _build_combos(profile: BrandProfile) -> list[tuple[Optional[str], Optional[str]]]:
    """Build the combo list from profile.creation_directions x copywriting_types.

    Capped at GEO_CONTENT_MAX_COMBOS_PER_QUERY (default 9) -- prevents NxM explosion.
    """
    dirs = [d for d in (profile.creation_directions or []) if d and d in DIRECTION_HINTS]
    types = [t for t in (profile.copywriting_types or []) if t and t in TYPE_HINTS]
    if not dirs and not types:
        return [(None, None)]  # Fallback: old path, 1 article
    if not dirs:
        return [(None, t) for t in types[:_MAX_COMBOS_PER_QUERY]]
    if not types:
        return [(d, None) for d in dirs[:_MAX_COMBOS_PER_QUERY]]
    out: list[tuple[Optional[str], Optional[str]]] = []
    for d in dirs:
        for t in types:
            out.append((d, t))
            if len(out) >= _MAX_COMBOS_PER_QUERY:
                return out
    return out


def _stamp_marks(db, docs) -> None:
    """Content labeling hook: write the three provenance columns (services/content_marking).

    **Must be called after the doc is committed and has a primary key** (the content id includes doc.id).
    Since 2026-08-17 the body gets no explicit label line / zero-width watermark; the provenance ledger (generator + IP + id) is still recorded.

    Write failures only warn, never raise -- the draft itself is already persisted, provenance columns can be backfilled later,
    but the whole batch of generation results must not be rolled back because they could not be written.
    """
    try:
        from geo.services import content_marking

        if content_marking.stamp_many(db, docs):
            db.commit()
    except Exception as e:  # noqa: BLE001
        log.warning("content marking failed: %s", e)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


def _compliance_scan_batch(db, t: AiTelemetryTopicORM, docs) -> None:
    """Compliance gate hook 1: scan right after generation is persisted (auto-reject on ban / missing mandatory disclaimer).

    See docs/06-发布运维/合规与资质/合规中心/合规审计中心-整体设计.md 5.2. Scan failures only warn and do not block generation.
    """
    try:
        from geo.services import compliance_scanner

        n_blocked = 0
        for d in docs:
            if not (d.body_markdown or "").strip():
                continue
            row = compliance_scanner.scan_and_apply(db, d, t, hook="generated")
            if row.verdict == "returned":
                n_blocked += 1
        db.commit()
        if n_blocked:
            log.info("compliance scan(generated): topic %d, %d docs auto-returned", t.id, n_blocked)
    except Exception:  # noqa: BLE001
        log.exception("compliance scan after generation failed for topic %s", t.id)
        db.rollback()


def _compose(
    *,
    profile: BrandProfile,
    topic_text: str,
    provider: str,
    api_key: str,
    direction: Optional[str] = None,
    copywriting_type: Optional[str] = None,
    topic_id: Optional[int] = None,
    style_refs: Optional[list] = None,
    cite_guidelines: Optional[list] = None,
    material_refs: Optional[list] = None,
    cover_queries: Optional[list[str]] = None,
    platform: str = "",
    platform_rules: str = "",
    tmpl: Optional[ContentTemplateORM] = None,
    seed: Optional[str] = None,
    competitors: str = "",
) -> tuple[str, str, str, Optional[dict]]:
    """Unified drafting exit -- returns (title, body, summary, quality).

    Non-empty tmpl takes the template path, otherwise the profile combo path (same branching as before the rework, decided by the caller).
    By default wraps a multi-agent refinement layer (services/content_quality.py): plan -> write -> review -> revise;
    with GEO_CONTENT_QUALITY=0 falls back to single-shot generation, quality is None, behavior identical to before the rework.
    """
    allow_md = bool(TYPE_HINTS.get(copywriting_type or "", {}).get("allow_md", False))
    # 2026-08-05 -- when the template itself requires Markdown (ranking roundups, and the trust / ranking / research report templates borrowed from open source
    # all rely on tables and FAQs as AI summary hooks), the refinement pipeline must not review against "Markdown strictly forbidden" -- otherwise
    # the review deducts points and the revise agent flattens tables and ## subheadings, zeroing out the template's structural advantage.
    if _template_allows_md(tmpl):
        allow_md = True
    if tmpl is not None and (tmpl.length_min or tmpl.length_max):
        len_spec = f"{tmpl.length_min or 800}-{tmpl.length_max or 1500} 字"
    else:
        len_spec = str(TYPE_HINTS.get(copywriting_type or "", {}).get("len") or "800-1500 字")

    def draft(outline_hint: str) -> tuple[str, str, str]:
        if tmpl is not None:
            return _generate_with_template(
                profile, tmpl, topic_text, platform, provider, api_key,
                seed=seed, style_refs=style_refs, cite_guidelines=cite_guidelines,
                material_refs=material_refs,
                cover_queries=cover_queries, platform_rules=platform_rules,
                outline_hint=outline_hint, competitors=competitors,
            )
        return _generate_one(
            profile, topic_text, provider, api_key,
            direction=direction, copywriting_type=copywriting_type,
            topic_id=topic_id, style_refs=style_refs,
            cite_guidelines=cite_guidelines, material_refs=material_refs,
            cover_queries=cover_queries,
            platform_rules=platform_rules, outline_hint=outline_hint,
        )

    if not content_quality.enabled():
        title, body, summary = draft("")
        return title, body, summary, None
    return content_quality.run(
        draft_fn=draft, provider=provider, api_key=api_key, profile=profile,
        topic_text=topic_text, cover_queries=cover_queries, platform=platform,
        type_label=(copywriting_type or (tmpl.kind if tmpl is not None else "") or ""),
        len_spec=len_spec, allow_md=allow_md, cite_guidelines=cite_guidelines,
    )


def _apply_quality(doc: TopicGeneratedDocORM, quality: Optional[dict]) -> None:
    """Persist the refinement pipeline's review result to the draft row (cleared when the pipeline is off, to avoid stale scores)."""
    doc.quality_score = (quality or {}).get("score")
    doc.quality_json = content_quality.dump(quality)


def insufficient_balance_msg(db, topic, planned_docs: int) -> Optional[str]:
    """Whether the balance is enough for this batch; if not, return a human-readable sentence, otherwise None.

    Extracted so the **API layer can also ask before dispatching the background task** -- inside a background task,
    discovering there is no money can only be logged; the frontend still got a 200, and the row would stay in "generating" forever.
    """
    need = topic_billing.price(db, PRICE_CONTENT_DOC) * max(0, int(planned_docs))
    gap = topic_billing.shortfall(db, topic, need)
    if gap is None:
        return None
    need_cents, have_cents = gap
    return (f"余额不足:本次需 {need_cents} 分({planned_docs} 篇),"
            f"当前余额 {have_cents} 分。充值后再重新生成。")


def _afford_or_stop(db, topic, planned_docs: int, trigger_source: str,
                    plan_item_ids: list[str] | None = None) -> bool:
    """Balance precheck before running. If insufficient, the whole batch does not start -- running out halfway leaves a pile of half-finished "generating" drafts,
    and the LLM quota is already burned. Enough / should not be charged (operator topic, price 0) always returns True.

    Estimated by article count only, excluding images and compliance -- those two default to 0 and failures do not block; including them would only make the precheck
    more likely to misjudge the balance as insufficient.

    **Must leave a trace when insufficient**: the caller (regenerate_plan_item) already set the draft row to
    `generating` before dispatching; if we only return False here, that row stays in "generating" forever --
    no error cause, and the frontend already got a 200, so operators just keep clicking regenerate. On 2026-08-27 the DB had 5
    such zombie rows, the oldest stuck since 7-30.
    """
    msg = insufficient_balance_msg(db, topic, planned_docs)
    if msg is None:
        return True
    log.warning("[billing] topic=%s insufficient balance, %d articles in this batch not started (trigger=%s)",
                topic.id, planned_docs, trigger_source)
    _fail_generating_docs(db, topic.id, msg, plan_item_ids=plan_item_ids)
    return False


def _fail_generating_docs(db, topic_id: int, msg: str,
                          plan_item_ids: list[str] | None = None) -> None:
    """Return draft rows involved in this run that are still `generating` to pending_review and write the error cause.

    Return to pending_review instead of inventing a new failed state: the frontend already renders failed rows everywhere as
    "pending_review + non-empty generation_error"; a new state would require changes all around.
    No automatic retry -- without money every retry is the same; gen_retry's quota class is terminal anyway.
    """
    try:
        q = (db.query(TopicGeneratedDocORM)
               .filter(TopicGeneratedDocORM.topic_id == topic_id,
                       TopicGeneratedDocORM.status == "generating",
                       TopicGeneratedDocORM.deleted_at.is_(None)))
        if plan_item_ids:
            q = q.filter(TopicGeneratedDocORM.plan_item_id.in_(list(plan_item_ids)))
        n = 0
        for d in q.all():
            d.status = "pending_review"
            d.generation_error = msg
            n += 1
        if n:
            db.commit()
            log.info("[billing] topic=%s returned %d \"generating\" rows with the error cause", topic_id, n)
    except Exception:  # noqa: BLE001
        db.rollback()
        log.exception("[billing] failed to roll back generating rows topic=%s", topic_id)


def _run_per_item(
    db, t: AiTelemetryTopicORM, profile: BrandProfile,
    topic_id: int, plan_id: int | None,
    plan_items: list[dict], mark_auto_run: bool,
    vscl_action_id: int | None = None,
    batch=None,
) -> None:
    """Draft one article per publishing_plan_items row.

    One item -> one doc. docs are upserted by (topic_id, plan_item_id):
    - existing doc -> overwrite title/body/summary, set status back to pending_review
    - no doc -> create new
    Template missing or nonexistent -> go straight to the old _generate_one (profile prompt fallback).
    """
    provider, model_id, api_key = _resolve_provider()
    style_refs = _load_style_refs(db, topic_id)
    cite_guidelines = _load_citation_playbook(db, topic_id)
    query_gap_map = _load_query_gap_map(db, topic_id)
    # Fallback index when a plan row has no prompts attached (same metric definition as the detail page, see plan_link)
    from geo.models.ai_telemetry import deleted_query_texts
    from geo.services.plan_link import _FALLBACK_COVER_QUERIES, seed_monitored_queries
    seed_queries = seed_monitored_queries(t)
    _gone_queries = deleted_query_texts(t)      # Soft-deleted terms: deleted after planning, still left on the row
    # Ranking / research report templates name peers; query only once per batch
    competitors = _load_competitors(
        db, topic_id, profile.company_short_name or profile.company_full_name or "")

    # Fetch templates in bulk to avoid N queries
    tmpl_ids = {it.get("template_id") for it in plan_items if it.get("template_id")}
    tmpl_by_id: dict[int, ContentTemplateORM] = {}
    if tmpl_ids:
        for tmpl in (
            db.query(ContentTemplateORM)
              .filter(ContentTemplateORM.id.in_(list(tmpl_ids)))
              .all()
        ):
            tmpl_by_id[tmpl.id] = tmpl

    # Existing doc index -- changed on 2026-05-28 to a (plan_item_id, creation_direction, copywriting_type)
    # triple key, supporting multiple combo variants (NxM) for one plan_item.
    # When a combo field is None, an empty string is stored as the key for easy lookup.
    def _doc_key(pid: Optional[str], d: Optional[str], t: Optional[str]) -> str:
        return f"{pid or ''}|{d or ''}|{t or ''}"
    existing_by_combo: dict[str, TopicGeneratedDocORM] = {}
    for d in (
        db.query(TopicGeneratedDocORM)
          .filter(TopicGeneratedDocORM.topic_id == topic_id)
          .filter(TopicGeneratedDocORM.plan_item_id.isnot(None))
          .all()
    ):
        if d.plan_item_id:
            existing_by_combo[_doc_key(d.plan_item_id, d.creation_direction, d.copywriting_type)] = d

    n_ok = 0
    n_created = 0
    img_budget: dict = {}
    touched_docs: list[TopicGeneratedDocORM] = []
    for it in plan_items:
        item_id = str(it.get("id") or "")
        q = str(it.get("query") or "").strip()
        seed = str(it.get("seed") or "").strip()
        # 2026-06-11 -- multi-select queries rows: this group of prompts must be naturally covered by the body; legacy single-query rows remain compatible.
        row_queries = [str(x).strip() for x in (it.get("queries") or []) if str(x).strip()]
        # ★ Plan row terms come from publishing_plan_json (frozen at planning time); terms soft-deleted afterwards
        #   still remain on the row -- without a check here they would still be fed to the LLM. This is the actual generation point for publishing.
        if _gone_queries:
            row_queries = [q for q in row_queries if q not in _gone_queries]
        cover_queries = list(row_queries)
        if not cover_queries and q:
            cover_queries = [q]
        # When a plan row has no prompts (old plans / the seed has fewer queries than articles per day), take a few from the seed on the fly,
        # otherwise this article is written generically from the seed without targeting any prompt -- a wasted article.
        if not cover_queries and seed:
            cover_queries = seed_queries.get(seed, [])[:_FALLBACK_COVER_QUERIES]
        # 2026-08-14 -- when a row has exactly 1 prompt, this article is written for that question, so use it directly as the topic term;
        # with multiple / 0 prompts, still write generically from the seed and naturally cover them (metric definition unchanged).
        # The check must use the row's original row_queries, not cover_queries -- the latter was just
        # filled by "row has no terms -> fall back to a few from the seed", and that fallback single term is not user-specified.
        if len(row_queries) == 1:
            topic_text = row_queries[0]
        else:
            # seed-based rows use the seed as topic; legacy rows fall back to query.
            topic_text = seed or q or (cover_queries[0] if cover_queries else "")
        platform = str(it.get("platform") or "") or None
        tmpl_id = it.get("template_id")
        tmpl = tmpl_by_id.get(tmpl_id) if tmpl_id else None
        if not topic_text:
            continue

        # 2026-05-28 -- per-row combo override. Non-empty on the row -> use the row's, otherwise the profile default.
        # Both empty -> single variant (None, None) takes the old path.
        row_dirs = [d for d in (it.get("creation_directions") or []) if d and d in DIRECTION_HINTS]
        row_types = [t for t in (it.get("copywriting_types") or []) if t and t in TYPE_HINTS]
        if row_dirs or row_types:
            # Row override: use only this row's (Cartesian product, empty sides filled with [None])
            dirs = row_dirs or [None]
            types = row_types or [None]
            combos: list[tuple[Optional[str], Optional[str]]] = []
            for dx in dirs:
                for tx in types:
                    combos.append((dx, tx))
                    if len(combos) >= _MAX_COMBOS_PER_QUERY:
                        break
                if len(combos) >= _MAX_COMBOS_PER_QUERY:
                    break
        else:
            combos = _build_combos(profile)

        for direction, copywriting_type in combos:
            ckey = _doc_key(item_id, direction, copywriting_type)
            doc = existing_by_combo.get(ckey)
            if doc is None:
                doc = TopicGeneratedDocORM(
                    topic_id=topic_id, execution_plan_id=plan_id,
                    plan_item_id=item_id or None,
                    template_id=tmpl.id if tmpl else None,
                    platform=platform,
                    source_query_text=topic_text, status="generating",
                    selected_for_review=True,
                    llm_model=model_id, source="ai",
                    creation_direction=direction,
                    copywriting_type=copywriting_type,
                    vscl_action_id=vscl_action_id,
                )
                db.add(doc)
                n_created += 1
            else:
                # Only overwrite the binding when this batch carries an action; None does not clear existing links (regenerate/normal batches carry no action)
                if vscl_action_id is not None:
                    doc.vscl_action_id = vscl_action_id
                # Regenerate: clear error + set generating, wait for the LLM to overwrite the body, then back to pending_review
                doc.template_id = tmpl.id if tmpl else None
                doc.platform = platform
                doc.source_query_text = topic_text
                doc.generation_error = None
                doc.status = "generating"
                doc.selected_for_review = True
                doc.llm_model = model_id
                doc.source = "ai"
            touched_docs.append(doc)
            doc._gen_queries = list(cover_queries)   # Snapshot of terms this article actually covers
            # 2026-07-31 -- persist "generating" before calling the LLM: when the publish plan table / content review page polls
            # it immediately sees this row running, instead of staying "not generated" until the whole batch ends.
            db.commit()
            gen_batch.stamp_docs(batch, [doc])      # Written once only when empty; regeneration does not overwrite

            if not provider:
                doc.title = f"[未生成] {topic_text}"
                doc.status = "pending_review"
                gen_retry.mark_failure(db, doc, "DEEPSEEK_API_KEY / OPENROUTER_API_KEY 都未配置")
                db.commit()
                continue
            selection = None
            old_orphan_ids: list[int] = []
            try:
                rules_block = _platform_rules_block(db, platform)
                # Single-variant old path uses the template; with a combo, use the profile path even if a template exists
                # (the template prompt does not accept combo injection, so go straight to profile combo)
                use_tmpl = tmpl if (tmpl and direction is None and copywriting_type is None) else None
                title, body, summary, quality = _compose(
                    profile=profile, topic_text=topic_text, provider=provider, api_key=api_key,
                    direction=direction, copywriting_type=copywriting_type,
                    topic_id=topic_id, style_refs=style_refs,
                    cite_guidelines=_merge_query_guidelines(
                        cite_guidelines, query_gap_map, cover_queries),
                    material_refs=_load_material_refs(db, t, topic_id, topic_text),
                    cover_queries=cover_queries,
                    platform=platform or "", platform_rules=rules_block,
                    tmpl=use_tmpl, seed=(seed or None), competitors=competitors,
                )
                selection, medias = _prepare_article_image(
                    db, t, profile, img_budget,
                    title=title, summary=summary, body=body, query=topic_text)
                _apply_quality(doc, quality)
                doc.title = title
                doc.body_markdown = _embed_media_images(body, medias)
                doc.summary = summary
                gen_retry.mark_success(doc)
                # Success: approved directly when review is off, manual queue when review is on
                doc.status = _ok_status(db, topic_id)
                old_orphan_ids = _finalize_article_image(db, t, doc, selection)
                # Billing: only charge when actually written. Idempotency key pins "batch x this article" --
                # rerunning the same batch hits the same key and is not charged again; regeneration opens a new batch so it is charged again (as it should).
                topic_billing.charge(
                    db, t, kind=KIND_CONTENT_DOC,
                    amount_cents=topic_billing.price(db, PRICE_CONTENT_DOC),
                    idem_key=f"doc:{getattr(batch, 'id', 0)}:{doc.id}",
                    ref_type="doc", ref_id=str(doc.id),
                    note={"platform": platform or "",
                          "trigger": getattr(batch, "trigger_source", "") or ""},
                )
                db.commit()
                n_ok += 1
            except Exception as e:  # noqa: BLE001
                db.rollback()
                _abort_article_image(selection)
                log.warning("content gen failed for item=%s combo=(%s,%s) topic=%d: %s",
                            item_id, direction, copywriting_type, topic_id, e)
                doc = db.get(TopicGeneratedDocORM, doc.id)
                gen_batch.stamp_docs(batch, [doc])
                doc.title = f"[生成失败] {topic_text}"
                gen_retry.mark_failure(db, doc, str(e))
                # Failures must also leave generating -- stay in the review queue with generation_error,
                # otherwise this row is stuck in "generating" forever.
                doc.status = "pending_review"
                db.commit()
            else:
                from geo.services.image_orphan_cleanup import cleanup_orphan_media_ids
                cleanup_orphan_media_ids(old_orphan_ids)
            # Primary key is ready, label right away -- one by one, so the page never shows unlabeled drafts before the batch finishes
            _stamp_marks(db, [doc])

    if mark_auto_run:
        t.auto_generate_last_run_at = datetime.utcnow()
    gen_batch.close_batch(
        db, batch, touched_docs,
        stats={"created": n_created, "regenerated": len(touched_docs) - n_created})
    db.commit()
    _compliance_scan_batch(db, t, touched_docs)
    log.info(
        "content gen done(per-item) topic %d: ok=%d / total=%d",
        topic_id, n_ok, len(plan_items),
    )


def regenerate_doc(db, doc: TopicGeneratedDocORM) -> TopicGeneratedDocORM:
    """Regenerate a single existing doc -- overwrite title/body/summary in place.

    Reuses the doc's own source_query_text / creation_direction / copywriting_type /
    template_id / platform to reproduce the article's original generation path as closely as possible:
      - template and no combo (direction/type both empty) -> template path
      - otherwise -> _generate_one (profile + combo injection)
    After writing, pick images based on the new body (_prepare_article_image).
    Success -> status back to pending_review, generation_error cleared; failure raises directly (the caller records the error).
    Runs synchronously (~20-60s per LLM call).
    """
    topic_id = doc.topic_id
    t = db.get(AiTelemetryTopicORM, topic_id)
    if not t:
        raise RuntimeError(f"topic {topic_id} not found")
    try:
        profile_data = json.loads(t.profile_json or "{}")
        if not isinstance(profile_data, dict):
            profile_data = {}
        profile = BrandProfile(**profile_data)
    except Exception:  # noqa: BLE001
        profile = BrandProfile()

    query = (doc.source_query_text or "").strip()
    if not query:
        raise RuntimeError("doc 缺少提示词文本,无法重新生成")

    provider, model_id, api_key = _resolve_provider()
    if not provider:
        raise RuntimeError("DEEPSEEK_API_KEY / OPENROUTER_API_KEY 都未配置")

    direction = doc.creation_direction
    copywriting_type = doc.copywriting_type
    tmpl = db.get(ContentTemplateORM, doc.template_id) if doc.template_id else None
    style_refs = _load_style_refs(db, topic_id)
    cite_guidelines = _merge_query_guidelines(
        _load_citation_playbook(db, topic_id), _load_query_gap_map(db, topic_id), [query])

    rules_block = _platform_rules_block(db, doc.platform)
    # Single variant uses the template; with a combo the template prompt does not accept injection, so use the profile path
    use_tmpl = tmpl if (tmpl and direction is None and copywriting_type is None) else None
    selection = None
    try:
        # Regeneration also goes through the multi-agent refinement -- manual regenerate usually means the quality was poor, so review matters most here
        title, body, summary, quality = _compose(
            profile=profile, topic_text=query, provider=provider, api_key=api_key,
            direction=direction, copywriting_type=copywriting_type,
            topic_id=topic_id, style_refs=style_refs,
            cite_guidelines=cite_guidelines,
            material_refs=_load_material_refs(db, t, topic_id, query),
            cover_queries=[query],
            platform=doc.platform or "", platform_rules=rules_block,
            tmpl=use_tmpl,
        )
        selection, medias = _prepare_article_image(
            db, t, profile, {}, title=title, summary=summary, body=body, query=query)
        _apply_quality(doc, quality)
        doc.body_markdown = _embed_media_images(body, medias)

        doc.title = title
        doc.summary = summary
        doc.llm_model = model_id
        gen_retry.mark_success(doc)
        doc.status = _ok_status(db, topic_id)
        doc.selected_for_review = True
        old_orphan_ids = _finalize_article_image(db, t, doc, selection)
        db.commit()
    except Exception:
        db.rollback()
        _abort_article_image(selection)
        raise
    from geo.services.image_orphan_cleanup import cleanup_orphan_media_ids
    cleanup_orphan_media_ids(old_orphan_ids)
    # Regeneration overwrites the body in place, washing away labels too -- must re-label after overwriting.
    # content_marking.apply_marks is idempotent (strips old labels first), so they never stack;
    # provenance columns that already have values are left alone, owner and first-generation IP stay as-is.
    _stamp_marks(db, [doc])
    db.refresh(doc)
    log.info("content regen done: doc=%d topic=%d", doc.id, topic_id)
    return doc


def describe_generation_inputs(db, doc: TopicGeneratedDocORM) -> dict:
    """Read-only recomputation: what was injected into the prompt when this draft was generated -- data source for the article detail "strategy basis".

    **Pure read, no LLM requests, no DB writes**. For the "strategy basis" block on the content detail page.

    Why in this file and not the API layer: the blocks below use the same functions as generation
    (_build_keyword_seo_block / _build_cover_queries_block / _platform_rules_block /
    _load_citation_playbook / _load_query_gap_map / _load_competitors). Keeping them together means
    if anyone changes the generation logic later, the explanation page does not silently become a lie.

    ⚠️ Metric definition: this is **recomputed from the current configuration**, not an archive from generation time. Platform red lines keep
    being appended by "learning from rejections", citation requirements change as the source strategy is regenerated, and the competitor list changes with batch runs,
    so the return value carries a caveat that the frontend must display as-is, so nobody mistakes it for a snapshot from that time.
    """
    topic_id = doc.topic_id
    t = db.get(AiTelemetryTopicORM, topic_id)
    profile = BrandProfile()
    if t is not None:
        try:
            data = json.loads(t.profile_json or "{}")
            if isinstance(data, dict):
                profile = BrandProfile(**data)
        except Exception:  # noqa: BLE001
            pass

    tmpl = db.get(ContentTemplateORM, doc.template_id) if doc.template_id else None
    query = (doc.source_query_text or "").strip()

    # The plan row for this draft -- covered questions, seed and platform follow the plan row (more complete than the single values on the doc)
    plan_row: Optional[dict] = None
    cover_queries: list[str] = [query] if query else []
    seed: Optional[str] = None
    if doc.plan_item_id and doc.execution_plan_id:
        plan = db.get(AiTelemetryTopicExecutionPlanORM, doc.execution_plan_id)
        if plan is not None:
            try:
                rows = json.loads(plan.publishing_plan_json or "[]")
            except Exception:  # noqa: BLE001
                rows = []
            for it in rows if isinstance(rows, list) else []:
                if isinstance(it, dict) and str(it.get("id") or "") == str(doc.plan_item_id):
                    plan_row = {
                        "item_id": it.get("id"), "seq": it.get("seq"),
                        "publish_date": it.get("publish_date"),
                        "seed": it.get("seed"), "queries": it.get("queries") or [],
                        "platform": it.get("platform"), "channel": it.get("channel"),
                        "media": it.get("media"), "note": it.get("note"),
                        "origin": it.get("origin"), "why": it.get("why"),
                    }
                    qs = [str(q).strip() for q in (it.get("queries") or []) if str(q).strip()]
                    if qs:
                        cover_queries = qs
                    seed = (it.get("seed") or "").strip() or None
                    break

    blocks: list[dict] = []

    # (1) Keyword targeting -- primary keyword / secondary keywords
    primary = (query or seed or "").strip()
    secondary = [x for x in ([seed] + list(cover_queries)) if x and x.strip() != primary]
    seen: set[str] = set()
    uniq: list[str] = []
    for x in secondary:
        x = x.strip()
        if x and x not in seen:
            seen.add(x)
            uniq.append(x)
    if primary:
        blocks.append({
            "key": "keyword_seo", "title": "关键词投放要求",
            "text": _build_keyword_seo_block(query, seed, cover_queries),
            "primary": primary, "secondary": uniq[:8], "source": "recomputed",
        })

    # (2) Questions this article must cover
    if cover_queries:
        blocks.append({
            "key": "cover_queries", "title": "本文必须覆盖的问题",
            "items": cover_queries, "source": "settled",
        })

    # (3) Platform review red lines -- count and update time are more informative than the text; full text only for operators
    if (doc.platform or "").strip():
        rules_text, rule_meta = "", {}
        try:
            from geo.models.ai_telemetry import PlatformRuleORM
            row = (db.query(PlatformRuleORM)
                     .filter(PlatformRuleORM.platform == doc.platform.strip()).first())
            if row is not None:
                rules_text = (row.rules_text or "").strip()
                rule_meta = {"rule_id": row.id,
                             "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                             "learned_at": row.learned_at.isoformat() if row.learned_at else None}
        except Exception:  # noqa: BLE001
            rules_text = ""
        if rules_text:
            lines = [ln.strip() for ln in rules_text.splitlines() if ln.strip()]
            blocks.append({
                "key": "platform_rules", "title": f"{doc.platform} 平台审核红线",
                "count": len(lines), "text": rules_text[:4000],
                "staff_only_text": True, "source": "live_config", **rule_meta,
            })

    # (4) Citation-friendly writing requirements (global) + (5) per-question citation gap plan
    guidelines = _load_citation_playbook(db, topic_id)
    if guidelines:
        blocks.append({
            "key": "citation_playbook", "title": "引用友好写作要求",
            "count": len(guidelines), "items": guidelines,
            "staff_only_text": True, "source": "live_config",
        })
    gap_map = _load_query_gap_map(db, topic_id)
    gap_hits = [{"query": q, "gap_plan": gap_map[q]} for q in cover_queries if q in gap_map]
    if gap_hits:
        blocks.append({
            "key": "query_gap_plan", "title": "问题级引用缺口方案",
            "items": gap_hits, "source": "live_config",
        })

    # (6) Peers named in the body -- taken from what the AI actually mentioned in batch runs, not made up
    competitors = _load_competitors(
        db, topic_id, profile.company_short_name or profile.company_full_name or "")
    if competitors:
        blocks.append({
            "key": "competitors", "title": "正文里会提到的同行",
            "text": competitors, "source": "recomputed",
        })

    # (7) Contact info allowlist -- the only contact info allowed in the article; if empty, "none allowed anywhere"
    contact_pairs = _contact_values(profile)
    blocks.append({
        "key": "contacts", "title": "允许出现的联系方式",
        "items": [f"{k}:{v}" for k, v in contact_pairs],
        "count": len(contact_pairs),
        "text": ("只有这几项能写进正文,其余电话 / 网址 / 微信 / 邮箱一律按硬伤打回"
                 if contact_pairs
                 else "品牌资料里没填任何联系方式,全文禁止出现电话 / 网址 / 微信 / 邮箱"),
        "source": "settled",
    })

    # (8) Own material library hits -- which user-uploaded passages this article actually retrieved
    try:
        from geo.services import material_rag
        mat = material_rag.retrieve(
            db, account_id=getattr(t, "user_id", 0) or 0, topic_id=topic_id,
            query=(doc.source_query_text or ""))
    except Exception:  # noqa: BLE001
        mat = []
    if mat:
        blocks.append({
            "key": "materials", "title": "引用的自有资料",
            "count": len(mat),
            "items": [{"title": m.get("title") or "", "source": m.get("source") or ""}
                      for m in mat],
            "text": ("语义检索(向量)" if mat[0].get("mode") == "semantic" else "关键词检索"),
            "source": "recomputed",
        })

    # (9) Style examples: how many own past articles were referenced, and how many of them were actually cited
    refs = _load_style_refs(db, topic_id)
    if refs:
        blocks.append({
            "key": "style_refs", "title": "参考的自有稿文风",
            "count": len(refs),
            "cited_count": sum(1 for r in refs if r.get("cited")),
            "items": [{"title": r.get("title") or "", "cited": bool(r.get("cited"))}
                      for r in refs],
            "source": "recomputed",
        })

    is_en = _is_english_template(tmpl) if tmpl is not None else False
    return {
        "doc_id": doc.id, "topic_id": topic_id,
        "generated_at": doc.created_at.isoformat() if doc.created_at else None,
        "recomputed_at": datetime.utcnow().isoformat(),
        "llm_model": doc.llm_model or "",
        # Single variant + template -> template path; creation preferences -> profile path (template prompts do not accept preference injection)
        "route": ("template"
                  if (tmpl is not None and not doc.creation_direction
                      and not doc.copywriting_type)
                  else "profile_combo"),
        "template": ({"id": tmpl.id, "name": tmpl.name, "kind": tmpl.kind,
                      "length_min": tmpl.length_min, "length_max": tmpl.length_max,
                      "english": is_en}
                     if tmpl is not None else None),
        "combo": {
            # Chinese names are mapped by the frontend (comboOptions.ts already has a dictionary); only keys and length metric definition here
            "creation_direction": doc.creation_direction,
            "copywriting_type": doc.copywriting_type,
            "length_hint": (TYPE_HINTS.get(doc.copywriting_type or "", {}) or {}).get("len"),
            "source": "plan_row" if plan_row else "profile_default",
        },
        "platform": doc.platform or "",
        "plan_row": plan_row,
        "blocks": blocks,
        "quality": {
            "score": getattr(doc, "quality_score", None),
            "detail": _parse_quality_safe(doc),
        },
        "caveat": "以上按现在的配置复算,与生成当时实际注入的内容可能有出入 —— "
                  "平台红线会随「从拒稿学习」追加、引用要求随信源策略重新生成而变。",
    }


def _parse_quality_safe(doc) -> Optional[dict]:
    try:
        raw = getattr(doc, "quality_json", None)
        data = json.loads(raw) if raw else None
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


# Pick only one user source image per article; use it directly the first time, then generate new images via image-to-image.
# 2026-09-29 -- image selection moved to after the article is written: first pick the most relevant source image by body (word overlap
# between asset description / filename and the article), then put the article topic into the image-to-image prompt. Previously images were rotated by use count
# before writing, with a fixed prompt, so images did not match the article content.
# Same day -- text-to-image fallback: when no source image is usable / image-to-image conditions are unmet / image-to-image fails, draw an
# illustration from title + summary. GEO_IMAGE_T2I_FALLBACK=0 turns it off, back to "real photos only".
_T2I_FALLBACK = os.environ.get("GEO_IMAGE_T2I_FALLBACK", "1").strip().lower() not in {"0", "false", ""}
_AUTO_IMAGE = os.environ.get("GEO_CONTENT_AUTO_IMAGE", "1").strip().lower() not in {"0", "false", ""}
_IMAGE_GEN_MAX_PER_RUN = int(os.environ.get("GEO_IMAGE_GEN_MAX_PER_RUN", "5"))
_IMAGE_CLAIM_TTL_SEC = max(300, int(os.environ.get("GEO_IMAGE_CLAIM_TTL_SEC", "7200")))
_IMAGE_EDIT_RETRY_BASE_SEC = 5 * 60
_IMAGE_EDIT_RETRY_MAX_SEC = 24 * 60 * 60
_RISKY = ("high", "medium")


@dataclass(frozen=True)
class _ArticleImageSelection:
    source_id: Optional[int]        # None = text-to-image fallback, no source image claimed
    claim_token: Optional[str]
    media_id: int
    generated: bool = False


def _eligible_article_images(db, topic_id: int, now: datetime):
    """Query for currently claimable safe user images."""
    return (
        db.query(TopicMediaORM)
          .filter(TopicMediaORM.topic_id == topic_id,
                  TopicMediaORM.kind == "image",
                  TopicMediaORM.is_ai_generated.is_(False))
          .filter(~func.coalesce(TopicMediaORM.external_risk, "").in_(_RISKY))
          .filter(or_(TopicMediaORM.claim_expires_at.is_(None),
                      TopicMediaORM.claim_expires_at <= now))
          .filter(or_(TopicMediaORM.image_edit_retry_at.is_(None),
                      TopicMediaORM.image_edit_retry_at <= now))
          .order_by(TopicMediaORM.use_count.asc(),
                    TopicMediaORM.uploaded_at.asc(),
                    TopicMediaORM.id.asc())
    )


def _select_article_image(db, topic_id: int, *, now: Optional[datetime] = None) \
        -> Optional[TopicMediaORM]:
    """Read-only view of the current best candidate; actual generation must go through `_claim_article_image`."""
    return _eligible_article_images(db, topic_id, now or datetime.utcnow()).first()


_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_ASCII_WORD_RE = re.compile(r"[a-z][a-z0-9]{2,}")


def _match_grams(text: str) -> set[str]:
    """Chinese split into adjacent character bigrams, English into words of >= 3 letters -- coarse, but no tokenizer dependency."""
    text = (text or "").lower()
    grams: set[str] = set()
    for run in _CJK_RUN_RE.findall(text):
        grams.update(run[i:i + 2] for i in range(len(run) - 1))
    grams.update(_ASCII_WORD_RE.findall(text))
    return grams


def _media_relevance(description: Optional[str], filename: Optional[str],
                     article_grams: set[str]) -> int:
    """Number of overlapping terms between asset description + filename and the article; 0 = no visible relevance."""
    if not article_grams:
        return 0
    stem = os.path.splitext(filename or "")[0]
    return len(_match_grams(f"{description or ''} {stem}") & article_grams)


def _claim_article_image(topic_id: int, *, article_text: str = "",
                         now: Optional[datetime] = None) \
        -> Optional[tuple[int, str]]:
    """Claim an image with a short CAS transaction, avoiding cross-process collisions on the same counter.

    Candidates are sorted by relevance to article_text descending; on ties (including all 0) still rotate by
    use count -> upload time, so asset libraries without descriptions behave as before.
    """
    claimed_at = now or datetime.utcnow()
    token = uuid.uuid4().hex
    db = SessionLocal()
    try:
        rows = (_eligible_article_images(db, topic_id, claimed_at)
                .with_entities(TopicMediaORM.id, TopicMediaORM.description,
                               TopicMediaORM.filename)
                .limit(200).all())
        grams = _match_grams(article_text)
        # sorted is stable: ties keep the use_count / uploaded_at order from the query
        ids = [row_id for row_id, _desc, _fn in
               sorted(rows, key=lambda r: -_media_relevance(r[1], r[2], grams))]
        for media_id in ids:
            updated = (
                db.query(TopicMediaORM)
                  .filter(TopicMediaORM.id == media_id,
                          TopicMediaORM.topic_id == topic_id,
                          TopicMediaORM.kind == "image",
                          TopicMediaORM.is_ai_generated.is_(False))
                  .filter(~func.coalesce(TopicMediaORM.external_risk, "").in_(_RISKY))
                  .filter(or_(TopicMediaORM.claim_expires_at.is_(None),
                              TopicMediaORM.claim_expires_at <= claimed_at))
                  .filter(or_(TopicMediaORM.image_edit_retry_at.is_(None),
                              TopicMediaORM.image_edit_retry_at <= claimed_at))
                  .update({
                      TopicMediaORM.claim_token: token,
                      TopicMediaORM.claim_expires_at:
                          claimed_at + timedelta(seconds=_IMAGE_CLAIM_TTL_SEC),
                  }, synchronize_session=False)
            )
            if updated == 1:
                db.commit()
                return media_id, token
        db.rollback()
        return None
    except Exception:  # noqa: BLE001
        db.rollback()
        log.warning("claim article image failed topic=%d", topic_id, exc_info=True)
        return None
    finally:
        db.close()


def _release_image_claim(selection: Optional[_ArticleImageSelection]) -> None:
    if selection is None or selection.source_id is None:
        return
    db = SessionLocal()
    try:
        (db.query(TopicMediaORM)
           .filter(TopicMediaORM.id == selection.source_id,
                   TopicMediaORM.claim_token == selection.claim_token)
           .update({TopicMediaORM.claim_token: None,
                    TopicMediaORM.claim_expires_at: None},
                   synchronize_session=False))
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        log.warning("release image claim failed source=%d", selection.source_id,
                    exc_info=True)
    finally:
        db.close()


def _record_image_edit_failure(source_id: int, claim_token: str,
                               *, now: Optional[datetime] = None) -> None:
    """Record consecutive failures of this reference image and release the lease."""
    db = SessionLocal()
    try:
        row = (db.query(TopicMediaORM)
                 .filter(TopicMediaORM.id == source_id,
                         TopicMediaORM.claim_token == claim_token)
                 .first())
        if row is None:
            return
        failures = int(row.image_edit_fail_count or 0) + 1
        delay = min(_IMAGE_EDIT_RETRY_BASE_SEC * (2 ** min(failures - 1, 9)),
                    _IMAGE_EDIT_RETRY_MAX_SEC)
        row.image_edit_fail_count = failures
        row.image_edit_retry_at = (now or datetime.utcnow()) + timedelta(seconds=delay)
        row.claim_token = None
        row.claim_expires_at = None
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        log.warning("record image edit failure failed source=%d", source_id,
                    exc_info=True)
    finally:
        db.close()


def _reset_image_edit_failures(source_id: int, claim_token: str) -> None:
    db = SessionLocal()
    try:
        (db.query(TopicMediaORM)
           .filter(TopicMediaORM.id == source_id,
                   TopicMediaORM.claim_token == claim_token)
           .update({TopicMediaORM.image_edit_fail_count: 0,
                    TopicMediaORM.image_edit_retry_at: None},
                   synchronize_session=False))
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        log.warning("reset image edit failures failed source=%d", source_id,
                    exc_info=True)
    finally:
        db.close()


_ARTICLE_MATCH_BODY_CHARS = 1500


def _prepare_article_image(db, topic, profile: BrandProfile, budget: dict, *,
                           title: str = "", summary: str = "", body: str = "",
                           query: str = "") \
        -> tuple[Optional[_ArticleImageSelection], list[TopicMediaORM]]:
    """Prepare this article's single image after it is written; the returned lease must be completed on success or released on failure by the caller.

    Order: pick the most relevant user source image by body -> use the source directly on first use / otherwise image-to-image (with the article topic);
    no source image, image-to-image conditions unmet or failed -> text-to-image fallback (illustration from title + summary).
    title / summary / body / query are of the article just written.
    """
    if not _AUTO_IMAGE:
        return None, []
    article_text = "\n".join(
        [title, query, summary, (body or "")[:_ARTICLE_MATCH_BODY_CHARS]])
    subject = "——".join(x for x in ((title or "").strip(), (summary or "").strip()) if x)
    claim = _claim_article_image(topic.id, article_text=article_text)
    if claim is not None:
        result = _edit_claimed_image(db, topic, profile, budget, claim, subject)
        if result is not None:
            return result
    return _text_image_fallback(db, topic, profile, budget,
                                title=(title or query), summary=summary)


def _edit_claimed_image(db, topic, profile: BrandProfile, budget: dict,
                        claim: tuple[int, str], subject: str) \
        -> Optional[tuple[_ArticleImageSelection, list[TopicMediaORM]]]:
    """Produce this article's image from the claimed source image; None = not produced (lease released / failure recorded), handed to the fallback."""
    source_id, claim_token = claim
    selection = _ArticleImageSelection(source_id, claim_token, source_id)
    source = db.get(TopicMediaORM, source_id)
    if source is None:
        _release_image_claim(selection)
        return None
    if int(source.use_count or 0) == 0:
        return selection, [source]
    if not (profile.ctrip_location.name or "").strip():
        # No Ctrip location filled in -> skip image-to-image and reuse the source image directly (no longer skipped as imageless).
        return selection, [source]
    if budget.get("used", 0) >= _IMAGE_GEN_MAX_PER_RUN:
        log.info("image edit budget exhausted (%d) topic=%d source=%d",
                 _IMAGE_GEN_MAX_PER_RUN, topic.id, source.id)
        _release_image_claim(selection)
        return None
    from geo.services import image_gen
    if not image_gen.available():
        _release_image_claim(selection)
        return None
    budget["used"] = budget.get("used", 0) + 1
    row_id = _run_image_gen(
        topic, "prepare article image",
        lambda image_db: image_gen.generate_article_image(
            image_db, topic_id=topic.id, user_id=topic.user_id,
            source_media=image_db.get(TopicMediaORM, source_id), subject=subject))
    if row_id is None:
        _record_image_edit_failure(source_id, claim_token)
        return None
    _reset_image_edit_failures(source_id, claim_token)
    generated = db.get(TopicMediaORM, row_id)
    if generated is None:
        _record_image_edit_failure(source_id, claim_token)
        from geo.services.image_orphan_cleanup import mark_orphan_and_delete
        mark_orphan_and_delete(row_id)
        return None
    return _ArticleImageSelection(
        source_id, claim_token, generated.id, generated=True), [generated]


def _text_image_fallback(db, topic, profile: BrandProfile, budget: dict, *,
                         title: str, summary: str) \
        -> tuple[Optional[_ArticleImageSelection], list[TopicMediaORM]]:
    """Text-to-image from title + summary when no source image is available; shares the per-batch image cap with image-to-image."""
    if not _T2I_FALLBACK or not (title or "").strip():
        return None, []
    if budget.get("used", 0) >= _IMAGE_GEN_MAX_PER_RUN:
        log.info("text image budget exhausted (%d) topic=%d",
                 _IMAGE_GEN_MAX_PER_RUN, topic.id)
        return None, []
    from geo.services import image_gen
    if not image_gen.available():
        return None, []
    budget["used"] = budget.get("used", 0) + 1
    row_id = _run_image_gen(
        topic, "text image fallback",
        lambda image_db: image_gen.generate_text_image(
            image_db, topic_id=topic.id, user_id=topic.user_id,
            title=title, summary=summary, industry=profile.industry or ""))
    if row_id is None:
        return None, []
    generated = db.get(TopicMediaORM, row_id)
    if generated is None:
        from geo.services.image_orphan_cleanup import mark_orphan_and_delete
        mark_orphan_and_delete(row_id)
        return None, []
    return _ArticleImageSelection(None, None, generated.id, generated=True), [generated]


def _run_image_gen(topic, what: str, gen) -> Optional[int]:
    """Image generation uses its own session: it commits asset rows itself and must not be pulled into the article transaction. Returns the new row id or None."""
    image_db = SessionLocal()
    try:
        row = gen(image_db)
        return row.id if row is not None else None
    except Exception:  # noqa: BLE001
        log.warning("%s crashed topic=%d — skipped", what, topic.id, exc_info=True)
        image_db.rollback()
        return None
    finally:
        image_db.close()


def _finalize_article_image(db, topic, doc: TopicGeneratedDocORM,
                            selection: Optional[_ArticleImageSelection]) -> list[int]:
    """Complete counting, binding and billing within the article transaction, and return old AI images to delete after commit."""
    if doc.id is None:
        db.flush()
    current_id = selection.media_id if selection and selection.generated else None
    old_rows = (
        db.query(TopicMediaORM)
          .filter(TopicMediaORM.generated_doc_id == doc.id,
                  TopicMediaORM.is_ai_generated.is_(True),
                  TopicMediaORM.generation_state == "attached")
          .all()
    )
    old_ids = [row.id for row in old_rows if row.id != current_id]
    for row in old_rows:
        if row.id != current_id:
            row.generation_state = "orphaned"

    if selection is None:
        db.flush()
        return old_ids

    if selection.source_id is not None:
        (db.query(TopicMediaORM)
           .filter(TopicMediaORM.id == selection.source_id)
           .update({TopicMediaORM.use_count: TopicMediaORM.use_count + 1},
                   synchronize_session=False))
        (db.query(TopicMediaORM)
           .filter(TopicMediaORM.id == selection.source_id,
                   TopicMediaORM.claim_token == selection.claim_token)
           .update({TopicMediaORM.claim_token: None,
                    TopicMediaORM.claim_expires_at: None},
                   synchronize_session=False))
    if selection.generated:
        media = db.get(TopicMediaORM, selection.media_id)
        if media is None or media.generation_state != "pending":
            raise RuntimeError(f"pending AI image {selection.media_id} not found")
        media.generated_doc_id = doc.id
        media.generation_state = "attached"
        topic_billing.charge(
            db, topic, kind=KIND_CONTENT_IMAGE,
            amount_cents=topic_billing.price(db, PRICE_CONTENT_IMAGE),
            idem_key=f"img:{media.id}", ref_type="media", ref_id=str(media.id),
            note={"source_media_id": selection.source_id},
        )
    db.flush()
    return old_ids


def _abort_article_image(selection: Optional[_ArticleImageSelection]) -> None:
    """Article failed: no counting, no billing; release the lease and clean up this run's AI images."""
    if selection is None:
        return
    _release_image_claim(selection)
    if selection.generated:
        from geo.services.image_orphan_cleanup import mark_orphan_and_delete
        mark_orphan_and_delete(selection.media_id)


def _insert_image_line(body: str, line: str) -> str:
    """Structured body with `## ` subheadings -> insert before the first subheading (after the opening hook paragraph);
    otherwise (short copy / voiceover script / no subheadings) append to the end, as before."""
    m = re.search(r"(?m)^##\s", body)
    if m is None or not body[:m.start()].strip():
        return f"{body.rstrip()}\n\n{line}"
    head = body[:m.start()].rstrip()
    return f"{head}\n\n{line}\n\n{body[m.start():]}"


def _embed_media_images(body: str, medias: list[TopicMediaORM]) -> str:
    """Keep exactly one standard Markdown reference to the selected image in the body.

    The writing model no longer knows about images (they are picked after writing), so normally the body does not contain this image,
    and it is inserted here by rule; the dedup logic remains to handle references already in templates / manual edits."""
    media = next((m for m in medias if (m.kind or "image") == "image"), None)
    if media is None:
        return body
    url = _media_public_url(media)
    pattern = re.compile(r"!\[[^\]\r\n]*\]\(\s*" + re.escape(url) + r"\s*\)")
    seen = False

    def _keep_first(match) -> str:
        nonlocal seen
        if seen:
            return ""
        seen = True
        return match.group(0)

    rendered = pattern.sub(_keep_first, body or "")
    if seen:
        return rendered
    return _insert_image_line(rendered, f"![文章配图 1]({url})")


def _render_template(prompt_template: str, vars: dict[str, object]) -> str:
    """Simple mustache substitution: {{key}} -> str(value). Unmatched placeholders are kept as-is."""
    s = prompt_template or ""
    for k, v in vars.items():
        s = s.replace("{{" + k + "}}", str(v if v is not None else ""))
    return s


def _build_products_block(profile: BrandProfile, *, max_items: int = 8, max_chars: int = 600) -> str:
    """Product list from section 3 of the materials -> prompt section (added 2026-09-22). The featured product goes first and is marked so the draft prioritizes it;
    no products filled -> empty string (generation for old materials unchanged)."""
    ps = featured_products([p.model_dump() for p in (profile.products or [])])
    if not ps:
        return ""
    lines: list[str] = []
    for p in ps[:max_items]:
        name = str(p.get("name") or "").strip()
        if not name:
            continue
        bits: list[str] = []
        if p.get("category"):
            bits.append(str(p["category"]).strip())
        sp = [str(x).strip() for x in (p.get("selling_points") or []) if str(x).strip()]
        if sp:
            bits.append("卖点:" + "、".join(sp[:3]))
        tu = [str(x).strip() for x in (p.get("target_users") or []) if str(x).strip()]
        if tu:
            bits.append("适合:" + "、".join(tu[:3]))
        tag = "【主推产品,稿件里优先植入】" if p.get("is_featured") else ""
        lines.append(f"- {tag}{name}" + (f"({';'.join(bits)})" if bits else ""))
    body = "\n".join(lines)
    if len(body) > max_chars:
        body = body[:max_chars].rstrip() + "…"
    return "品牌自有产品(只能写这些,不要编造型号或参数):\n" + body


def _featured_product_var(profile: BrandProfile) -> str:
    """Template variable {{featured_product}}: featured product name + one-line selling point; no featured product -> empty string."""
    for p in profile.products or []:
        if p.is_featured and p.name.strip():
            sp = [x.strip() for x in (p.selling_points or []) if x.strip()]
            return p.name.strip() + (f"({'、'.join(sp[:3])})" if sp else "")
    return ""


def _build_brand_block(profile: BrandProfile) -> str:
    """Profile summary -- for the {{brand_block}} placeholder in templates. More compact than _build_system_prompt."""
    parts: list[str] = []
    if profile.brand_diff_tags:
        parts.append(f"品牌差异化:{', '.join(profile.brand_diff_tags)}")
    products_block = _build_products_block(profile)
    if products_block:
        parts.append(products_block)
    if profile.content_tones:
        parts.append(f"调性:{', '.join(profile.content_tones)}")
    if profile.content_redlines:
        parts.append(f"内容雷区(必须避开):{', '.join(profile.content_redlines)}")
    if profile.brand_slogan:
        parts.append(f"Slogan:{profile.brand_slogan}")
    if profile.core_message:
        parts.append(f"核心信息:{profile.core_message}")
    return "\n".join(parts)


def _platform_rules_block(db, platform: Optional[str]) -> str:
    """2026-06-12 -- fetch review red lines by publish platform (platform_rules table) and render them as a prompt injection section.

    Empty platform / no rules configured -> empty string (generation unchanged).
    """
    p = (platform or "").strip()
    if not p:
        return ""
    try:
        from geo.models.ai_telemetry import PlatformRuleORM
        row = db.query(PlatformRuleORM).filter(PlatformRuleORM.platform == p).first()
    except Exception:  # noqa: BLE001 -- do not block generation when the table has not been migrated yet
        return ""
    rules = (row.rules_text or "").strip() if row else ""
    if not rules:
        return ""
    return (
        f"\n\n【{p} 平台审核红线 — 违反任何一条稿件会被平台拒审,必须全部遵守】\n"
        f"{rules[:4000]}"
    )


# Default rank for "which position the featured product takes" in the open-source template (ai-content-forge) -- our existing industry roundups
# also place it 4th: too early looks like a hard ad, too late nobody reads that far.
_RANK_POSITION = int(os.environ.get("GEO_CONTENT_RANK_POSITION", "4"))


def _build_keyword_seo_block(query: str, seed: Optional[str],
                             cover_queries: Optional[list[str]] = None) -> str:
    """{{keyword_seo}} -- keyword targeting instruction (following ai-content-forge's buildKeywordSEOInstruction).

    Primary keyword is the search term; secondary keywords are the seed + the prompts this article covers.
    """
    primary = (query or seed or "").strip()
    secondary = [x for x in ([seed] + list(cover_queries or [])) if x and x.strip() != primary]
    # Dedupe preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for x in secondary:
        x = x.strip()
        if x and x not in seen:
            seen.add(x)
            uniq.append(x)
    if not primary:
        return ""
    lines = [
        "【关键词投放要求】",
        f"- 核心关键词「{primary}」:标题、开头段、结尾段各出现 1 次,全文合计 3-5 次,自然融入不堆砌",
    ]
    if uniq:
        lines.append("- 次要关键词(各出现 1-2 次,放在对应小节里,不合适就不放):"
                     + "、".join(uniq[:8]))
    return "\n".join(lines)


def _load_competitors(db, topic_id: int, brand: str = "", limit: int = 5) -> str:
    """{{competitors}} -- peers actually mentioned by AI in the topic's recent batch runs, TOP N by mention count.

    Data comes from ai_telemetry_responses.competitors_json (extracted asynchronously after batch runs); returns empty string if none,
    and the template side falls back to "write generically about common industry solutions". Queries the latest 300 rows, representative enough without a full table scan.
    """
    try:
        from geo.models.ai_telemetry import AiTelemetryResponseORM
        rows = (
            db.query(AiTelemetryResponseORM.competitors_json)
              .filter(AiTelemetryResponseORM.topic_id == topic_id)
              .filter(AiTelemetryResponseORM.competitors_json.isnot(None))
              .order_by(AiTelemetryResponseORM.id.desc())
              .limit(300)
              .all()
        )
    except Exception:  # noqa: BLE001
        log.warning("load competitors failed topic=%s — skipped", topic_id, exc_info=True)
        return ""
    tally: dict[str, int] = {}
    brand_norm = (brand or "").strip()
    for (raw,) in rows:
        try:
            items = json.loads(raw or "[]")
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(items, list):
            continue
        for it in items:
            name = str(it.get("name") if isinstance(it, dict) else it or "").strip()
            if not name or (brand_norm and brand_norm in name):
                continue
            tally[name] = tally.get(name, 0) + int(
                (it.get("count") if isinstance(it, dict) else 1) or 1)
    top = sorted(tally.items(), key=lambda kv: -kv[1])[:limit]
    return "、".join(n for n, _ in top)


def _template_allows_md(tmpl: Optional[ContentTemplateORM]) -> bool:
    """Does the template itself require Markdown -- the criterion is whether the prompt mentions Markdown.

    An allow_md column would be cleaner, but would need changes to migration + API + frontend; template copy always spells out
    the "output format", so this criterion is sufficient and zero-risk for old templates (old templates all say "严禁 Markdown" ("Markdown strictly forbidden"),
    which also contains the word markdown -- so explicitly forbidding phrasings are excluded).
    """
    if tmpl is None:
        return False
    p = (tmpl.prompt_template or "").lower()
    if "markdown" not in p:
        return False
    if "严禁 markdown" in p or "严禁使用 markdown" in p or "严禁任何 markdown" in p:
        return False
    return True


def _is_english_template(tmpl: Optional[ContentTemplateORM]) -> bool:
    """English template criterion -- kind is an English type, or the name contains (English)."""
    if tmpl is None:
        return False
    return (tmpl.kind or "").strip() in ("英文长文", "英文榜单") or "(english" in (tmpl.name or "").lower()


def _build_cover_queries_block(cover_queries: Optional[list[str]], topic_text: str = "",
                               english: bool = False) -> str:
    """Coverage requirement section for multi-select queries rows -- appended to the end of the user prompt.

    Returns empty string when there is a single query identical to the topic (legacy single-query row), to avoid redundancy.
    """
    qs = [q for q in (cover_queries or []) if q and q != topic_text]
    if not qs:
        return ""
    bullets = "\n".join(f"  - {q}" for q in qs[:20])
    if english:
        # English templates: wrap the whole section in English -- it sits at the very end of the user prompt, and Chinese wrapper words would
        # pull the model back into Chinese (measured: the English long-form template was dragged back by this section). The questions are Chinese prompts;
        # keep the original text and ask the model to express them in English itself.
        return (
            "\n\nThe article must naturally cover the following real user questions "
            "(they are written in Chinese — translate them and answer each one inside the "
            "article body in English; weave them into the prose, do not list them verbatim):\n"
            + bullets
        )
    return (
        f"\n\n本文必须自然覆盖以下用户真实搜索问题(围绕它们组织内容,"
        f"在正文中逐一给出解答或信息;融进行文,不要把问题原文罗列成清单):\n{bullets}"
    )


def _generate_with_template(
    profile: BrandProfile, tmpl: ContentTemplateORM,
    query: str, platform: str,
    provider: str, api_key: str,
    seed: Optional[str] = None,
    style_refs: Optional[list] = None,
    cite_guidelines: Optional[list] = None,
    material_refs: Optional[list] = None,
    cover_queries: Optional[list[str]] = None,
    platform_rules: str = "",
    outline_hint: str = "",
    competitors: str = "",
) -> tuple[str, str, str]:
    """Template path -- the system prompt still uses the brand profile, the user prompt uses template rendering.

    When seed is non-empty (seed-based plan), {seed} in the template gets the seed text and {query} gets a copy of seed for compatibility with
    old templates; legacy plan rows have seed None, and both variables get query.
    When cover_queries is non-empty, a "question group to cover naturally" section is appended after rendering (multi-select query rows).
    """
    # English templates do not get own-article style examples -- those examples are Chinese drafts, placed at the end of the system prompt
    # as the "strongest style anchor", so the model would write in Chinese (measured: that is exactly how the English long-form template failed).
    is_en = _is_english_template(tmpl)
    system_prompt = _build_system_prompt(profile,
                                         style_refs=(None if is_en else style_refs),
                                         cite_guidelines=cite_guidelines,
                                         material_refs=material_refs)
    brand_block = _build_brand_block(profile)
    now = datetime.utcnow() + timedelta(hours=8)   # Beijing time; "{{year}}年" ("{{year}}") in titles uses the local year
    user_prompt = _render_template(tmpl.prompt_template, {
        "query": query,
        "seed": seed or query,
        "brand": profile.company_short_name or profile.company_full_name or "",
        "industry": profile.industry or "",
        "platform": platform or (profile.target_platforms[0] if profile.target_platforms else ""),
        "length_min": tmpl.length_min,
        "length_max": tmpl.length_max,
        "brand_block": brand_block,
        # 2026-08-05 -- variables brought in by open-source templates (GEOFlow / ai-content-forge)
        "year": now.year,
        "year_month": f"{now.year}年{now.month}月",
        "region": profile.service_geo or "不限",
        "product_desc": (profile.core_service_overview or "").strip()[:600]
                        or (profile.brand_slogan or ""),
        # 2026-09-22 -- products marked "featured" in section 3 of the materials (name + selling point); empty if none, harmless if the template does not use it
        "featured_product": _featured_product_var(profile),
        "competitors": competitors or "(暂无采集到的竞品,按行业常见方案泛写,不要点名具体品牌)",
        "keyword_seo": _build_keyword_seo_block(query, seed, cover_queries),
        "rank_position": _RANK_POSITION,
    })
    user_prompt += _build_cover_queries_block(cover_queries, topic_text=query, english=is_en)
    # Platform writing rules are in Chinese; not appended for English templates (website blog / Medium have no corresponding Chinese platform rules)
    if not is_en:
        user_prompt += platform_rules
    # Multi-agent refinement: the outline agent's output is appended as a hard constraint (empty string = old single-shot generation, behavior unchanged)
    user_prompt += outline_hint
    # English templates: the system prompt is all Chinese; without a firm line the model replies in Chinese (measured failure on #16)
    if is_en:
        system_prompt = (
            "WRITE EVERYTHING IN ENGLISH. The brand profile below is in Chinese; "
            "translate what you need and never output Chinese in your answer.\n\n"
        ) + system_prompt + (
            "\n\nIMPORTANT: This assignment must be written entirely in English. "
            "The brand profile above is in Chinese — translate what you need. "
            "The title, summary and body fields must all be English; keep proper nouns as-is."
        )
    return _call_llm(system_prompt, user_prompt, provider, api_key,
                     allow_md=_template_allows_md(tmpl))


def _endpoint(provider: str, api_key: str) -> tuple[str, str, dict]:
    """(url, model, headers) -- the only difference between direct DeepSeek and OpenRouter lives here."""
    if provider == "deepseek":
        return (
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            DEEPSEEK_MODEL,
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
    return (
        OPENROUTER_URL,
        OPENROUTER_DEEPSEEK_MODEL,
        {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://www.vigilath.com",
            "X-Title": "GEO Content Generator",
        },
    )


def chat_json(system_prompt: str, user_prompt: str, provider: str, api_key: str,
              *, max_tokens: Optional[int] = None, feature: str = "content_gen.quality") -> dict:
    """Generic JSON chat exit -- used by the multi-agent refinement pipeline (services/content_quality.py).

    Difference from _call_llm: no assumption about returned fields (that one requires title/body/summary),
    the parsed dict is handed back to the caller as-is. Retry / truncation detection shared via _post_chat.
    """
    url, model, headers = _endpoint(provider, api_key)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.4,          # Outline / review should be stable, not divergent
        "max_tokens": max_tokens or MAX_TOKENS,
        "response_format": {"type": "json_object"},
    }
    return _parse_json_loose(_post_chat(url, headers, payload, provider, feature=feature))


def _post_chat(url: str, headers: dict, payload: dict, provider: str,
               *, feature: str = "content_gen") -> str:
    """Send one /chat/completions and return message.content -- with transient-error backoff retries.

    Retry metric definition: network-level exceptions (timeout / connection dropped) and status codes in _RETRY_STATUS;
    errors where retries are useless, like 402 balance / 401 key, are raised directly to the caller (the retry queue treats them as terminal).
    finish_reason=length means the output was truncated; raise a separate readable error (retrying is useless, max_tokens must be adjusted).

    Accounting (geo/services/llm_usage.py): **one row per retry round**, attempt_no increments,
    call_group_id is shared -- the "call count" on the page must reconcile with the provider bill; retries really cost money.
    Truncation (finish_reason=length) and response body parse failures raise, but the provider has already billed,
    so they are still recorded with usage, just with a different status.
    """
    last: Exception | None = None
    model = str(payload.get("model") or "")
    group = uuid.uuid4().hex[:16]

    def _rec(status: str, t0: float, attempt: int, *, usage=None,
             http_status: int | None = None, error_kind: str = "", error=None) -> None:
        record_llm_call(
            provider=provider, model=model, feature=feature, call_kind="chat",
            usage=usage, status=status, error_kind=error_kind, http_status=http_status,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            attempt_no=attempt, endpoint=url, call_group_id=group,
            extra={"error": str(error)[:200]} if error is not None else None,
        )

    for attempt in range(HTTP_RETRIES + 1):
        if attempt:
            time.sleep(HTTP_BACKOFF_SEC * (2 ** (attempt - 1)))
            log.info("[content-gen] %s retry #%d (last: %s)", provider, attempt, last)
        t0 = time.perf_counter()
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=DEFAULT_TIMEOUT)
        except requests.RequestException as e:
            _rec(STATUS_NETWORK_ERROR, t0, attempt,
                 error_kind="timeout" if "timeout" in type(e).__name__.lower() else "network", error=e)
            last = RuntimeError(f"{provider} 请求失败: {type(e).__name__}: {str(e)[:160]}")
            continue
        if r.status_code in _RETRY_STATUS:
            _rec(STATUS_HTTP_ERROR, t0, attempt, http_status=r.status_code,
                 error_kind=f"http_{r.status_code}", error=r.text[:200])
            last = RuntimeError(f"{provider} {r.status_code}: {r.text[:200]}")
            continue
        if r.status_code >= 400:
            _rec(STATUS_HTTP_ERROR, t0, attempt, http_status=r.status_code,
                 error_kind=f"http_{r.status_code}", error=r.text[:200])
            raise RuntimeError(f"{provider} {r.status_code}: {r.text[:200]}")
        data = None
        try:
            data = r.json()
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except Exception as e:  # noqa: BLE001
            # 200 but the response body is not the expected shape -- the provider has billed; record whatever usage is available
            _rec(STATUS_PARSE_ERROR, t0, attempt, http_status=r.status_code,
                 usage=data if isinstance(data, dict) else None,
                 error_kind="parse", error=e)
            raise RuntimeError(f"unexpected {provider} response shape: {e}")
        if (choice.get("finish_reason") or "") == "length":
            _rec(STATUS_TRUNCATED, t0, attempt, http_status=r.status_code, usage=data,
                 error_kind="truncated")
            budget = int(payload.get("max_tokens") or MAX_TOKENS)
            # Only escalate when attempts remain -- otherwise continue drops straight out of the loop,
            # and the raised error would say "escalated retry" although nothing was resent
            if budget < MAX_TOKENS_RETRY and attempt < HTTP_RETRIES:
                log.warning("[content-gen] %s output truncated (max_tokens=%d), retrying once with %d",
                            provider, budget, MAX_TOKENS_RETRY)
                payload = {**payload, "max_tokens": MAX_TOKENS_RETRY}
                last = RuntimeError(f"{provider} 输出被截断(max_tokens={budget}),已升档重试")
                continue
            raise RuntimeError(
                f"{provider} 输出被截断(finish_reason=length,max_tokens={budget})"
                " — 已按最大预算重试过,要缩短文章长度(模板字数/条目数)")
        _rec(STATUS_OK, t0, attempt, http_status=r.status_code, usage=data)
        return content
    raise last or RuntimeError(f"{provider} 请求失败")


def _call_llm(
    system_prompt: str, user_prompt: str,
    provider: str, api_key: str,
    allow_md: bool = False,
) -> tuple[str, str, str]:
    """OpenAI-compatible /chat/completions call + JSON parsing + Markdown fallback cleanup.

    With allow_md=True the body is kept as-is -- ranking / trust templates rely entirely on ## subheadings, tables and FAQs
    as AI summary hooks; stripping them all zeroes out the template's structural advantage (before 2026-08-05 it stripped unconditionally,
    flattening even the ### entries and bold required by the existing "industry roundup" template). Title / summary are still cleaned:
    those two fields go into lists and cards, where symbols only render as garbage.
    """
    url, model, headers = _endpoint(provider, api_key)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.7,
        "max_tokens": MAX_TOKENS,
        "response_format": {"type": "json_object"},
    }
    content = _post_chat(url, headers, payload, provider, feature="content_gen.article")
    parsed = _parse_json_loose(content)
    title = _strip_md_inline(str(parsed.get("title") or ""))[:200]
    raw_body = str(parsed.get("body") or "")
    body = raw_body if allow_md else _strip_md_block(raw_body)
    summary = _strip_md_inline(str(parsed.get("summary") or ""))[:500]
    if not title or not body:
        raise RuntimeError("LLM returned empty title/body")
    return title, body, summary


def _generate_one(
    profile: BrandProfile, query: str, provider: str, api_key: str,
    *,
    direction: Optional[str] = None,
    copywriting_type: Optional[str] = None,
    topic_id: Optional[int] = None,
    style_refs: Optional[list] = None,
    cite_guidelines: Optional[list] = None,
    material_refs: Optional[list] = None,
    cover_queries: Optional[list[str]] = None,
    platform_rules: str = "",
    outline_hint: str = "",
) -> tuple[str, str, str]:
    """Single query -> (title, body_markdown, summary).

    provider:
      - "deepseek":  direct https://api.deepseek.com/chat/completions, model=deepseek-chat
      - "openrouter": via https://openrouter.ai, model=deepseek/deepseek-chat

    2026-05-28 -- direction / copywriting_type parameters:
      - non-empty: inject a form-constraint section per combo; types with allow_md=True allow markdown
      - images no longer go into the writing prompt: images are picked by body after writing and inserted by code (_embed_media_images)
      - all empty: fall back to the 2026-05-18 "clean typeset text" old path, behavior unchanged
    """
    system_prompt = _build_system_prompt(
        profile, direction=direction, copywriting_type=copywriting_type,
        style_refs=style_refs,
        cite_guidelines=cite_guidelines, material_refs=material_refs,
    )
    allow_md = bool(TYPE_HINTS.get(copywriting_type or "", {}).get("allow_md", False))
    # Length comes from TYPE_HINTS; if the combo gives none, use the old 800-1500 char default
    type_cfg = TYPE_HINTS.get(copywriting_type or "", {})
    body_len_spec = str(type_cfg.get("len") or "800-1500 字")

    if allow_md:
        # Markdown friendly: allows # headings / **bold** / ![](url) embedded images, no longer stripped
        body_rules = (
            f"  1. 可以使用 Markdown 标记(# 标题 / ## 二级 / **加粗** / 列表 / ![图片](url) / [链接](url));\n"
            f"  2. 如果上面给了图片素材,**必须在正文一个自然位置嵌入一次 ![描述](素材url)**;\n"
            f"  3. 段落之间空一行,文章首尾要有钩子段和总结段;\n"
            f"  4. 标点用中文全角,英文术语 / 数字保留原样。"
        )
    else:
        # Old rule: markdown strictly forbidden, friendly for direct platform publishing
        body_rules = (
            f"  1. 严禁使用 Markdown / HTML 标记 — 不要出现 # ## ### **加粗** *斜体* `代码` > 引用 [链接](url) ![图片] 等任何符号;\n"
            f"  2. 严禁使用 - * 1. 等列表前缀;需要分点时用「一、二、三、」或「①②③」中文序号开头另起一段;\n"
            f"  3. 小标题独占一行、不加任何符号修饰,正文段落与小标题之间空一行;\n"
            f"  4. 段落与段落之间用一个空行分隔,段内不要硬换行;\n"
            f"  5. 所有标点用中文全角(,。!?:;「」),英文术语 / 数字保留原样即可。"
        )

    cover_block = _build_cover_queries_block(cover_queries, topic_text=query)
    user_prompt = (
        f"针对下面这个{'主题' if cover_block else '问题'},写一篇符合资料调性、可以直接复制发布的文章。\n"
        f"{'主题' if cover_block else '问题'}:{query}"
        f"{cover_block}{platform_rules}\n\n"
        f"输出严格 JSON,字段:\n"
        f'  "title": 文案标题(吸睛,≤30 字,纯文本不要带任何符号修饰),\n'
        f'  "summary": 200 字内的摘要(纯文本,用于卡片预览),\n'
        f'  "body": 文章正文({body_len_spec})。\n\n'
        f"正文排版强制要求(违反会导致稿件不可用):\n"
        f"{body_rules}\n\n"
        f"只输出 JSON,不要包前后 ``` 围栏。"
        f"{outline_hint}"
    )
    url, model, headers = _endpoint(provider, api_key)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.7,
        "max_tokens": MAX_TOKENS,
        # Both DeepSeek/OpenRouter support OpenAI-style response_format; DeepSeek requires the word "json" in the user
        # message; the end of our prompt explicitly says "只输出 JSON" ("output JSON only"), which satisfies it
        "response_format": {"type": "json_object"},
    }
    content = _post_chat(url, headers, payload, provider, feature="content_gen.article")
    parsed = _parse_json_loose(content)
    title = _strip_md_inline(str(parsed.get("title") or ""))[:200]
    raw_body = str(parsed.get("body") or "")
    # 2026-05-28 -- types with allow_md=True (long_form / medium_post / faq_list) keep markdown,
    # otherwise strip to plain text per the old rule. title / summary always strip inline symbols (used in cards, no MD allowed).
    body = raw_body if allow_md else _strip_md_block(raw_body)
    summary = _strip_md_inline(str(parsed.get("summary") or ""))[:500]
    if not title or not body:
        raise RuntimeError("LLM returned empty title/body")
    return title, body, summary


# ----------- Markdown fallback cleanup -----------
# The prompt explicitly forbids MD, but the LLM occasionally sneaks in `**xxx**` / `# Title` / `- item`,
# which would show up as raw symbols on official accounts / Xiaohongshu if persisted as-is. Do a lightweight regex cleanup here:
# strip only **safe** structural symbols, never touch body text. Brute-force stripping is not the goal -- the goal is that "to the human eye
# it reads like plain text".

import re as _re

# Leading # / ## / ### / ####...  -> keep the text on its own line (LLMs usually use these as subheadings)
_RE_HEADING = _re.compile(r"(?m)^\s{0,3}#{1,6}\s+")
# Leading  -  /  *  /  •  /  · / digit. / digit) -- list prefixes, removed; the body uses "一、" ("1.") Chinese numbering
_RE_LIST_PREFIX = _re.compile(r"(?m)^\s{0,3}([\-\*•·]|\d+[.)、])\s+")
# Leading > quote
_RE_BLOCKQUOTE = _re.compile(r"(?m)^\s{0,3}>\s?")
# **bold** / __bold__ -> text
_RE_BOLD = _re.compile(r"\*\*([^*\n]+?)\*\*|__([^_\n]+?)__")
# *italic* / _italic_ -> text (avoid treating a lone * as a list prefix; handled earlier)
_RE_ITALIC = _re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)|(?<!_)_([^_\n]+?)_(?!_)")
# `code` -> text
_RE_INLINE_CODE = _re.compile(r"`([^`\n]+?)`")
# ```fence``` keep the whole content, strip only the fence
_RE_FENCE = _re.compile(r"```[a-zA-Z0-9]*\n?([\s\S]*?)```")
# [text](link) -> text; ![alt](link) -> alt
_RE_LINK = _re.compile(r"!?\[([^\]\n]*?)\]\([^)\n]*?\)")
# Table separator row |---|---|
_RE_TABLE_SEP = _re.compile(r"(?m)^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$")


def _strip_md_inline(s: str) -> str:
    """For single lines -- title / summary: strip inline symbols + collapse multiple spaces."""
    s = _RE_BOLD.sub(lambda m: m.group(1) or m.group(2) or "", s)
    s = _RE_ITALIC.sub(lambda m: m.group(1) or m.group(2) or "", s)
    s = _RE_INLINE_CODE.sub(r"\1", s)
    s = _RE_LINK.sub(r"\1", s)
    return s.strip()


def _strip_md_block(s: str) -> str:
    """For the body -- strip block-level symbols first (headings/lists/quotes/fences/table separators), then inline cleanup.
    Finally compress 3+ consecutive blank lines to 2, to avoid exploding paragraph whitespace.
    """
    s = _RE_FENCE.sub(r"\1", s)
    s = _RE_HEADING.sub("", s)
    s = _RE_BLOCKQUOTE.sub("", s)
    s = _RE_LIST_PREFIX.sub("", s)
    s = _RE_TABLE_SEP.sub("", s)
    s = _RE_BOLD.sub(lambda m: m.group(1) or m.group(2) or "", s)
    s = _RE_ITALIC.sub(lambda m: m.group(1) or m.group(2) or "", s)
    s = _RE_INLINE_CODE.sub(r"\1", s)
    s = _RE_LINK.sub(r"\1", s)
    s = _re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# ----------- 2026-05-28 -- creation direction x copy type multi-variant generation -----------

# 7 creation directions -- each value maps to a writing guide. profile.creation_directions is multi-selected from this enum.
DIRECTION_HINTS: dict[str, str] = {
    "industry_insight":  "以行业趋势 / 市场数据 / 头部玩家动向切入,产出有信息密度的分析。"
                         "避免泛泛而谈,每个论点配 1 个数据 / 案例 / 引述支撑。",
    "case_story":        "围绕一个真实案例展开:背景 → 挑战 → 我方解法 → 量化结果 → 启发。"
                         "案例主角用资料里的 case_stories 或 customer 真实名字,不要虚构。",
    "how_to_guide":      "用步骤化叙述:Step 1 / Step 2 / Step 3 ...,每步要有可执行动作 + 判断节点。"
                         "适合「怎么做 / 怎么选」类意图查询,先给框架再展开。",
    "trend_forecast":    "用前瞻视角:近 12-36 个月可能发生的变化 + 应对建议。"
                         "至少引用 1 个权威信号(政策 / 头部公司动作 / 监管文件)。",
    "product_review":    "对比 2-3 个同类产品 / 方案,列优劣矩阵,给出推荐场景。"
                         "评测维度 ≥ 4 个(价格 / 性能 / 适配 / 服务),保持客观语气。",
    "customer_story":    "第一人称引述客户原话(标注客户姓名 + 行业),用 2-3 段还原使用场景 + 量化成果。"
                         "篇幅 60% 给客户故事,40% 给我方点评 / 关联业务。",
    "faq":               "8-12 条常见问题 + 每题 100-200 字回答,问题先于回答。"
                         "问题口语化(模拟真实用户提问),回答专业精炼。",
    # -- GEO long-form strategy directions (2026-07-27, learned from high-citation manual templates). All paired with long_form for in-depth long articles --
    "geo_listicle":      "行业榜单型深度长文(软性推广):做成客观的选型指南,含一个 5-8 维对比表,"
                         "把本品牌放对比表第 2-4 位(不占第 1),除表格与其所在段落外不做硬广式提及;"
                         "候选厂商用市面真实存在的同类品牌,严禁瞎编厂商名;各品牌段篇幅均衡,本品牌段不超其它 1.5 倍。",
    "geo_brand_deepdive": "品牌深度解读(第三方研究视角):像行业研究报道那样客观深度地解读本品牌,"
                         "本品牌内容占 60-70%,其余为赛道背景/方法论/对比参照;用「产品力-渠道力-品牌力」等公认框架当骨架,"
                         "不用「我们推荐/首选/毫无疑问」等推销词,用事实/数据/对比让读者自行得出结论。",
    "geo_tech_explainer": "技术科普耦合(技术认知→品牌认知):先客观科普一项技术(含定义框 + 编号步骤列表),技术科普占 55-65%;"
                         "按「埋线(不点名铺垫)→照镜(引出本品牌为落地案例)→耦合(把品牌能力对应技术选型标准)」三步自然引出本品牌(占 25-35%);"
                         "品牌引出须合乎逻辑不生硬;若品牌资料未提及该技术则换品牌确有的技术方向,不强绑。",
}

# 6 copy types -- determine length / form constraints + whether markdown (image embedding) is allowed.
# With 'allow_md' = True the prompt does not force plain text and may embed Markdown images / bold;
# with False the old "clean typeset text" rule still applies.
TYPE_HINTS: dict[str, dict[str, object]] = {
    "long_form":          {"len": "1500-2500 字", "allow_md": True,
                           "hint": "正文 3-5 个二级小节,每节 300-500 字,带过渡句。"
                                   "首段 100-150 字钩子,尾段总结 + 行动召唤。"},
    "medium_post":        {"len": "500-1500 字", "allow_md": True,
                           "hint": "正文 2-3 个二级小节,适合公众号 / 知乎中等阅读量帖子。"
                                   "首段开门见山,正文每节 1 个核心观点。"},
    "short_social":       {"len": "≤500 字", "allow_md": False,
                           "hint": "短社媒文案:无小节,1-3 个 emoji,1 个 CTA。"
                                   "首句 3 秒抓眼球,适合小红书 / 微博。"},
    "video_script_long":  {"len": "5-8 分钟口播稿(2000-3000 字)", "allow_md": False,
                           "hint": "段落用 [镜头] / [口播] / [屏幕字] 标注。"
                                   "前 10 秒钩子吸引完播,中段问题铺陈,尾段解决方案 + CTA。"},
    "video_script_short": {"len": "30-60 秒口播稿(150-300 字)", "allow_md": False,
                           "hint": "前 3 秒钩子(疑问 / 痛点) → 5 秒共鸣 → 15 秒解法 → 5 秒 CTA。"
                                   "用短句,口语化,禁书面词。"},
    "faq_list":           {"len": "8-12 条 Q&A(总长 1000-2000 字)", "allow_md": True,
                           "hint": "纯 Q&A 列表,每条问题独占一行(用「Q:」前缀),"
                                   "回答 100-200 字(用「A:」前缀)。问题口语化,回答专业。"},
}


# -- Hard rules for AI citation friendliness (2026-07-27, learned from high-citation manual GEO templates) --
# Makes ChatGPT / Doubao / Kimi / Perplexity / Gemini prefer to fetch and directly cite this article when answering related questions.
# Only injected for long_form in-depth articles (short copy/video scripts cannot fit these structures). On by default; GEO_CONTENT_GEO_RULES=0 turns it off.
_GEO_RULES_ENABLED = os.environ.get("GEO_CONTENT_GEO_RULES", "1").strip().lower() not in {"0", "false", ""}
_GEO_RULES_TYPES = {"long_form"}

_GEO_RULES_BLOCK = """
## AI 引用友好度硬性规则(必须遵守 — 让大模型优先抓取并引用本文)
1. 标题规范:H1 及各级小标题末尾一律不加句号,且标题要自然嵌入用户高频搜索词。
2. 黄金 30 字核心句:每个二级标题(##)正下方,先用一句 ≤30 字、可独立引用的**加粗结论**开篇(有信息量的洞察,不是套话)。
3. 全文必含以下 4 类高引用片段:
   - 定义句:正文出现一次「XX 是指……」式精准定义,独立成引用块(> 开头)。
   - 多维对比表:至少 1 个 Markdown 表格,含 5-8 个对比维度(高信任度信号)。
   - FAQ 区块:文末设 3-5 组 Q/A,问题贴合真实搜索意图(靠谱吗 / 贵不贵 / 和竞品比),**每个回答严格 ≤80 字**。
   - 关键引用块:文末一个 30-80 字的引用块(> 开头),升华全文核心观点。
4. 段落有深度:每段展开充分,避免通篇 1-2 句的碎片段;若涉及多个主体/品牌,各段篇幅大致均衡。
5. 权威性:开篇可用行业赛道的市场规模 / 增长趋势作背景(引用信通院 / CNNIC / 艾瑞等机构或公开报告的**方向性结论**);可引入一个公认方法论或评估框架当分析骨架;写一个「场景—做法—结果」三段式典型案例。
6. 事实红线(最高优先级):涉及本品牌的一切具体信息(成立时间、参数、案例、客户、价格)只能基于上方品牌资料,严禁杜撰;**不得编造归属于具体机构的精确统计数字**——拿不准的数据改用方向性 / 区间表述,不要伪造来源。
""".rstrip()


def _build_geo_rules_block(copywriting_type: Optional[str]) -> str:
    """GEO citation-friendly hard rules section; only injected for in-depth long articles, other types return ''."""
    if not _GEO_RULES_ENABLED:
        return ""
    if (copywriting_type or "") not in _GEO_RULES_TYPES:
        return ""
    return _GEO_RULES_BLOCK


def _build_combo_block(direction: Optional[str], copywriting_type: Optional[str]) -> str:
    """Render the combo hint section into the system prompt. Both empty -> return '' (old path)."""
    if not direction and not copywriting_type:
        return ""
    lines: list[str] = ["", "## 本次稿件的形式要求"]
    if direction:
        h = DIRECTION_HINTS.get(direction, "")
        if h:
            lines.append(f"- 创作方向「{direction}」: {h}")
        else:
            lines.append(f"- 创作方向: {direction}")
    if copywriting_type:
        cfg = TYPE_HINTS.get(copywriting_type, {})
        len_ = cfg.get("len", "")
        hint = cfg.get("hint", "")
        if len_ or hint:
            lines.append(f"- 文案类型「{copywriting_type}」: 篇幅 {len_};{hint}")
        else:
            lines.append(f"- 文案类型: {copywriting_type}")
    return "\n".join(lines)


def _media_public_url(media: "TopicMediaORM", base: str = "") -> str:
    """Convert storage_path -> public URL.

    storage_path format: .../data/topic_media/{topic_id}/{32hex}.{ext}
    Returns: {base}/api/ai-telemetry/public/topic-media/{topic_id}/{32hex}.{ext}

    Empty base (default) -> relative path, used directly by the frontend on the same origin / behind nginx reverse proxy.
    Set the GEO_PUBLIC_MEDIA_BASE env var -> absolute URL (publishing to official accounts / Zhihu needs absolute https).
    """
    from geo.services import media_storage
    base = base or os.environ.get("GEO_PUBLIC_MEDIA_BASE", "").rstrip("/")
    return media_storage.public_url(media, base)


# ----------- Own-article style learning (few-shot) -----------
# 2026-06-09 -- old generation only fed the brand profile and never referenced user-imported own articles, so drafts'
# style/structure/rhythm did not match the user's own writing (e.g. for Zhang's topic, the user uploaded several own articles, but AI
# drafts read like someone else wrote them). Here own articles under the same topic are injected as style examples into the system
# prompt, so the LLM imitates their style/structure/paragraph rhythm/opening hook/ending rather than copying content.
# 2026-08-04 -- closed-loop feedback: **drafts actually cited by AI engines** rank ahead of own articles (see _load_style_refs).
# Still never take "AI drafts that were never cited" -- that is what causes self-reinforcing drift.
_STYLE_REF_MAX = int(os.environ.get("GEO_CONTENT_STYLE_REF_MAX", "2"))
_STYLE_REF_BODY_CHARS = int(os.environ.get("GEO_CONTENT_STYLE_REF_CHARS", "1800"))


def _cited(doc: TopicGeneratedDocORM) -> bool:
    """Whether this draft was actually cited by an AI engine (non-empty cited_by_json = matched by backfill)."""
    try:
        v = json.loads(getattr(doc, "cited_by_json", None) or "{}")
        return bool(isinstance(v, dict) and v)
    except Exception:  # noqa: BLE001
        return False


def _load_material_refs(db, t, topic_id: int, query: str) -> list[dict]:
    """Search the account's own material library (agent_materials) by the current question -- any failure counts as none.

    Materials belong to the **account** (t.user_id), not the topic: company intros and product specs a customer uploaded
    should be usable by every topic; this topic's materials get extra weight in material_rag.
    """
    try:
        from geo.services import material_rag
        refs = material_rag.retrieve(
            db, account_id=getattr(t, "user_id", 0) or 0,
            topic_id=topic_id, query=query)
        if refs:
            log.info("material rag: topic=%s hit=%d mode=%s", topic_id, len(refs),
                     refs[0].get("mode"))
        return refs
    except Exception:  # noqa: BLE001
        log.warning("material rag failed topic=%s -- skipping material injection", topic_id, exc_info=True)
        return []


def _load_style_refs(db, topic_id: int, limit: int = _STYLE_REF_MAX) -> list[dict]:
    """Load style examples: **drafts actually cited by AI** first, then user-imported own articles.

    2026-08-04 -- closed-loop feedback: previously only source="user" was accepted (fearing AI drafts as examples would cause self-reinforcing drift).
    But "actually cited by an engine" is the only objective positive example on this pipeline -- it already passed the AI's selection;
    using it as an example is not self-reinforcement but feeding measured results back to generation. So the order becomes:

        (1) cited drafts (any source, the result of AI voting with its feet)
        (2) user's own articles that are approved / published
        (3) other user's own articles

    Still **never takes** AI drafts that were never cited -- that would be self-reinforcement.
    Returns [{title, body, cited}], body truncated to _STYLE_REF_BODY_CHARS to control tokens.
    """
    if limit <= 0:
        return []
    try:
        rows = (
            db.query(TopicGeneratedDocORM)
              .filter(TopicGeneratedDocORM.topic_id == topic_id)
              .filter(TopicGeneratedDocORM.body_markdown != "")
              .order_by(TopicGeneratedDocORM.created_at.desc())
              .all()
        )
    except Exception:  # noqa: BLE001
        log.warning("load style refs failed topic=%d — skipped", topic_id, exc_info=True)
        return []
    _PREFERRED = ("published", "approved")
    pool: list[tuple[int, TopicGeneratedDocORM]] = []
    for d in rows:
        cited = _cited(d)
        own = (getattr(d, "source", "") or "") == "user"
        if cited:
            rank = 0
        elif own and (d.status or "") in _PREFERRED:
            rank = 1
        elif own:
            rank = 2
        else:
            continue          # Never-cited AI drafts are not used as examples (avoid self-reinforcement)
        pool.append((rank, d))
    if not pool:
        return []
    pool.sort(key=lambda x: x[0])
    refs: list[dict] = []
    for rank, d in pool[:limit]:
        body = (d.body_markdown or "").strip()
        if not body:
            continue
        if len(body) > _STYLE_REF_BODY_CHARS:
            body = body[:_STYLE_REF_BODY_CHARS].rstrip() + "……(略)"
        refs.append({"title": (d.title or "").strip(), "body": body, "cited": rank == 0})
    return refs


def _build_style_ref_block(style_refs: Optional[list]) -> str:
    """Assemble own-article examples into the system prompt's "style imitation" section."""
    if not style_refs:
        return ""
    lines = [
        "",
        "─── 自有文章文风范例(必须严格模仿)───",
        "下面是该品牌已导入的自有文章。请你深度学习并模仿它们的**文风、结构、",
        "段落节奏、开头钩子、结尾收束、句式长短、用词习惯、口吻语气**,让本次出稿",
        "读起来像同一个作者、同一个品牌写出来的。注意:",
        "  - 只学「怎么写」,不要照抄范例里的具体内容/事实/数据/案例;",
        "  - 本次主题以上面的「问题 / 核心信息」为准,不要被范例的主题带跑;",
        "  - 范例怎么分段、怎么起标题、用不用小标题、口语还是书面,都尽量对齐。",
    ]
    for i, ref in enumerate(style_refs, 1):
        title = ref.get("title") or "(无标题)"
        body = ref.get("body") or ""
        # Examples actually cited by AI engines are highlighted -- they are the only objective positive examples on this pipeline,
        # letting the model know "writing like this is most likely to get cited".
        tag = "(★ 这篇已被 AI 引擎实际引用过,优先对齐它的写法)" if ref.get("cited") else ""
        lines.append(f"\n【范例 {i}】{tag}标题:{title}")
        lines.append(f"正文:\n{body}")
    lines.append("─── 范例结束 ───")
    return "\n".join(lines)


# ----------- Contact info gate (2026-08-10) -----------
# Phone / website / WeChat in drafts used to have no field source at all -- the prompt never provided them, so the model
# invented one from the prior that "brand copy should have a CTA", or copied an old one from style example bodies.
# Wrong contact info in a published article is an incident, so there are only two states here:
#   profile filled in -> provide the allowlist verbatim, and forbid any contact info outside it
#   profile empty -> forbid any contact info and "contact us" style CTAs anywhere
# The QA side has another deterministic block (content_quality._contact_violations); a draft ships only after passing both.

def _contact_values(profile: BrandProfile) -> list[tuple[str, str]]:
    """Contact info filled in the profile -- [(label, value)], fixed order so the prompt can be reused stably."""
    pairs = [
        ("官网", (profile.website or "").strip()),
        ("联系电话", (getattr(profile, "contact_phone", "") or "").strip()),
        ("微信 / 公众号", (getattr(profile, "contact_wechat", "") or "").strip()),
        ("邮箱", (getattr(profile, "contact_email", "") or "").strip()),
    ]
    return [(k, v) for k, v in pairs if v]


def _build_contact_block(profile: BrandProfile) -> str:
    allowed = _contact_values(profile)
    lines = ["", "─── 联系方式硬约束(违反即为事故稿)───"]
    if allowed:
        for k, v in allowed:
            lines.append(f"  - {k}:{v}")
        lines.append("正文里如果需要出现联系方式,**只能原样使用上面这几项**,一个字符都不能改;")
        lines.append("除此之外严禁出现任何其它电话号码、网址 / 域名、二维码、微信号、邮箱、地址 ——")
        lines.append("包括「示例」「如 400-XXX-XXXX」这类占位写法也不允许。")
    else:
        lines.append("本品牌**没有提供任何联系方式**。全文严禁出现电话号码、网址 / 域名、")
        lines.append("二维码、微信号 / 公众号、邮箱、门店地址,也不要写「联系我们」「扫码咨询」")
        lines.append("「立即预约」「详情请访问」这类需要联系方式兜底的 CTA —— 编一个出来")
        lines.append("就是给客户发错联系方式,后果比少一句 CTA 严重得多。")
    lines.append("─── 约束结束 ───")
    return "\n".join(lines)


# ----------- User material library grounding (2026-08-10) -----------
# agent_materials holds real materials the user uploaded (company intro / product specs / service descriptions).
# Previously only the chat agent could query them, not the drafting pipeline, so "real materials exist but it still makes things up".
# Here a few passages are retrieved by the current query and injected; facts follow them; when retrieval is empty the section is omitted.

def _build_material_block(material_refs: Optional[list]) -> str:
    if not material_refs:
        return ""
    lines = [
        "",
        "─── 品牌自有资料(用户上传,事实以此为准)───",
        "下面是该品牌自己提供的资料片段。凡与本文相关的事实、数字、名称、资质、",
        "服务口径,**一律以这里为准**,不要用你自己的先验去改写或补全;",
        "资料里没有的事实就不要写,更不要因为「读起来更完整」而补一个。",
    ]
    for i, ref in enumerate(material_refs, 1):
        title = (ref.get("title") or "").strip() or "(无标题资料)"
        src = (ref.get("source") or "").strip()
        lines.append(f"\n【资料 {i}】{title}{f' · 来源:{src}' if src else ''}")
        lines.append((ref.get("snippet") or "").strip())
    lines.append("─── 资料结束 ───")
    return "\n".join(lines)


# ----------- Citation-friendly writing requirements (flywheel P1-E) -----------
# Writing requirements produced after the source strategy (source_strategy) analyzes "why the model cites these articles",
# injected into the generation prompt so drafts are more likely to be cited by AI engines. Silently skipped when no strategy exists.
_CITE_PLAYBOOK_ENABLED = os.environ.get("GEO_CONTENT_CITE_PLAYBOOK", "1") == "1"
_CITE_PLAYBOOK_MAX = int(os.environ.get("GEO_CONTENT_CITE_PLAYBOOK_MAX", "8"))


def _load_citation_playbook(db, topic_id: int) -> list[str]:
    """Get the citation-friendly writing requirements from the topic's latest source strategy; empty if not generated/disabled."""
    if not _CITE_PLAYBOOK_ENABLED:
        return []
    try:
        from geo.services.source_strategy import latest_content_guidelines
        return latest_content_guidelines(db, topic_id)[:_CITE_PLAYBOOK_MAX]
    except Exception:  # noqa: BLE001
        log.warning("load citation playbook failed topic=%d — skipped", topic_id, exc_info=True)
        return []


def _load_query_gap_map(db, topic_id: int) -> dict[str, str]:
    """Per-question citation pool plan {query: gap_plan} (produced by source strategy query_source_insights)."""
    if not _CITE_PLAYBOOK_ENABLED:
        return {}
    try:
        from geo.services.source_strategy import latest_query_gaps
        return latest_query_gaps(db, topic_id)
    except Exception:  # noqa: BLE001
        log.warning("load query gap map failed topic=%d — skipped", topic_id, exc_info=True)
        return {}


def _merge_query_guidelines(base: Optional[list], gap_map: dict[str, str],
                            queries: Optional[list]) -> Optional[list]:
    """When questions covered by this article match a per-question plan, inject the dedicated gap_plan ahead of the global requirements."""
    if not gap_map or not queries:
        return base
    matched = [f"本文要覆盖的问题「{q}」:{gap_map[q]}"
               for q in queries if q and q in gap_map]
    if not matched:
        return base
    return matched + (base or [])


def _build_citation_block(cite_guidelines: Optional[list]) -> str:
    """Assemble citation-friendly writing requirements into a system prompt section."""
    if not cite_guidelines:
        return ""
    lines = [
        "",
        "─── 引用友好写作要求(基于 AI 引擎实际引用行为分析)───",
        "以下要求来自对各 AI 引擎实际引用信源的分析,遵守它们能显著提升本文被",
        "AI 搜索引擎引用的概率:",
    ]
    for g in cite_guidelines:
        lines.append(f"  - {g}")
    lines.append("─── 要求结束 ───")
    return "\n".join(lines)


def _build_system_prompt(profile: BrandProfile,
                          direction: Optional[str] = None,
                          copywriting_type: Optional[str] = None,
                          style_refs: Optional[list] = None,
                          cite_guidelines: Optional[list] = None,
                          material_refs: Optional[list] = None) -> str:
    """Materials -> system prompt, picking fields relevant to "copywriting".

    2026-05-28 -- added combo parameters: when direction / copywriting_type are non-empty, append a form-constraint section.
      (Images do not go into the prompt -- they are picked by body after writing, see _prepare_article_image.)
    2026-05-31 -- inject the current year and month, so the LLM does not use "this year" from its training cutoff (stale titles like 2024 are common).
    """
    from datetime import date
    today = date.today()
    parts: list[str] = [
        f"今天日期是 {today.year} 年 {today.month} 月。文中所有「今年」「最新」「近期」「趋势」等时间引用必须用 {today.year} 而不是你训练数据里的旧年份(如 2024)。",
        "",
        "你是品牌内容文案专家,根据下面的品牌资料写文案稿:",
    ]
    if profile.company_full_name:
        parts.append(f"- 品牌全称:{profile.company_full_name}")
    if profile.company_short_name:
        parts.append(f"- 品牌简称:{profile.company_short_name}")
    if profile.industry:
        parts.append(f"- 行业:{profile.industry}")
    if profile.service_geo:
        parts.append(f"- 服务地域:{profile.service_geo}")
    if profile.core_business_lines:
        parts.append(f"- 核心业务:{', '.join(profile.core_business_lines)}")
    products_block = _build_products_block(profile)
    if products_block:
        parts.append("- " + products_block.replace("\n", "\n  "))
    if profile.brand_diff_tags:
        parts.append(f"- 品牌差异化标签:{', '.join(profile.brand_diff_tags)}")
    if profile.creation_directions:
        parts.append(f"- 创作方向:{', '.join(profile.creation_directions)}")
    if profile.copywriting_types:
        parts.append(f"- 文案类型偏好:{', '.join(profile.copywriting_types)}")
    if profile.target_platforms:
        parts.append(f"- 适配平台:{', '.join(profile.target_platforms)}")
    if profile.content_tones:
        parts.append(f"- 内容调性:{', '.join(profile.content_tones)}")
    if profile.content_redlines:
        parts.append(f"- 内容雷区(禁止):{', '.join(profile.content_redlines)}")
    if profile.brand_slogan:
        parts.append(f"- Slogan:{profile.brand_slogan}")
    if profile.core_message:
        parts.append(f"- 本次核心信息:{profile.core_message}")
    parts.append("")
    parts.append("严格遵守内容雷区,语气贴合调性,围绕本次核心信息展开。")
    # Contact info gate: after brand fields, before form constraints -- it is a hard constraint, not a style suggestion
    parts.append(_build_contact_block(profile))
    # User material grounding: fact anchors must come before style examples, so old facts in examples do not override real materials
    material_block = _build_material_block(material_refs)
    if material_block:
        parts.append(material_block)
    # combo enhancement section (2026-05-28)
    combo_block = _build_combo_block(direction, copywriting_type)
    if combo_block:
        parts.append(combo_block)
    # Hard rules for AI citation friendliness (2026-07-27) -- in-depth long articles only; after form requirements, before dynamic citation suggestions
    geo_block = _build_geo_rules_block(copywriting_type)
    if geo_block:
        parts.append(geo_block)
    # Citation-friendly writing requirements (flywheel P1-E) -- before style examples: structural requirements set the rules first
    cite_block = _build_citation_block(cite_guidelines)
    if cite_block:
        parts.append(cite_block)
    # Own-article style examples (2026-06-09) -- placed last, as the strongest style anchor
    style_block = _build_style_ref_block(style_refs)
    if style_block:
        parts.append(style_block)
    return "\n".join(parts)


def _parse_json_loose(text: str) -> dict:
    """Extract JSON from LLM output, tolerating ``` fences or surrounding text."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.lstrip("`").lstrip()
        if s.lower().startswith("json"):
            s = s[4:].lstrip()
        if s.endswith("```"):
            s = s[:-3].rstrip()
    # strict=False tolerates real newlines / tabs inside strings -- the most common failure for Markdown bodies (tables, lists,
    # ## subheadings) is the model writing newlines verbatim into JSON strings, which strict mode rejects as invalid.
    for candidate in (s,):
        for strict in (True, False):
            try:
                v = json.loads(candidate, strict=strict)
                if isinstance(v, dict):
                    return v
            except Exception:  # noqa: BLE001
                continue
    # Fallback: regex-grab the first { } block
    import re
    m = re.search(r"\{[\s\S]*\}", s)
    if m:
        for strict in (True, False):
            try:
                v = json.loads(m.group(0), strict=strict)
                if isinstance(v, dict):
                    return v
            except Exception:  # noqa: BLE001
                continue
    raise RuntimeError(
        f"failed to parse JSON from LLM output (len={len(s)}): "
        f"head={s[:160]!r} tail={s[-160:]!r}")
