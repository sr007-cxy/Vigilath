"""Method layer — deterministic side effects, **not exposed to the LLM** (called from inside tools or by the orchestration layer).

No metered billing: usage_guardrail is our own cost/resource guardrail, unrelated to customer charges (charges go through offline contracts).
"""
from __future__ import annotations

import os

from sqlalchemy.orm import Session

from agents.geo.deps import AgentDeps
from geo.models.user import UserORM


def resolve_account(
    current_user: UserORM,
    db: Session,
    topic_id: int | None = None,
    page_context: dict | None = None,
    *,
    scope: str = "self",
    turn_id: str = "",
    user_message: str = "",
) -> AgentDeps:
    """Build the tenant context after authentication. Uses the existing user system: account_id = current_user.id.

    scope is decided by api.py's agents.geo.scope.resolve_scope() and passed in, **not decided here**
    (that side looks at role + master switch + route, a whole set of criteria that shouldn't be split across two places).
    """
    from agents.geo.scope import _role_of

    role = _role_of(current_user)
    return AgentDeps(
        account_id=current_user.id, user_id=current_user.id, db=db,
        topic_id=topic_id, page_context=page_context,
        role=role,
        actor_name=(getattr(current_user, "email", "") or getattr(current_user, "name", "") or ""),
        scope=scope,
        # Internal channels: one tier for the ops console, one for the web (both unlimited, but audits are kept separate)
        channel=("admin" if scope == "admin" else "web"),
        # The admin line locks the project: no fallback. The self line doesn't set it, keeping topic_id's soft-hint semantics.
        locked_topic_id=(topic_id if scope == "admin" else None),
        turn_id=turn_id,
        user_message=user_message,
    )


def _conv_query(deps: AgentDeps):
    from geo.models.agent import AgentConversationORM

    return (
        deps.db.query(AgentConversationORM)
        .filter(AgentConversationORM.account_id == deps.account_id)
        .filter(AgentConversationORM.scope == (deps.scope or "self"))
    )


# ── History pairing repair ──────────────────────────────────────────────
#
# tool_call (in a ModelResponse) and tool_return (in the **next** ModelRequest) are two messages.
# Any index-slicing trim may split them, and the consequences in both directions aren't errors but hard-to-debug faults:
#
#   · Head cut -> a tool_return can't find its tool_call. Mapped to the provider that's "starts with two
#     role=tool messages, no preceding assistant with tool_calls", and OpenAI-compatible endpoints return 400
#     tool_call_id not found. The old _truncate cut **at save time** and persisted right after,
#     so every later turn of that session got 400 — showing up as "this session suddenly errors forever until reset".
#   · Tail cut -> a tool_call has no tool_return. pydantic-ai raises UserError:
#     Cannot provide a new user prompt when the message history contains
#     unprocessed tool calls (_agent_graph.py). A timed-out turn has exactly this shape.
#
# So any trim must be followed by this function. It's a pure function, no DB, 100% unit-testable.


def _tool_linked(part) -> bool:
    """Whether this part depends on some tool_call existing.

    A RetryPromptPart with tool_name None is an "ordinary retry", not bound to a tool, and doesn't take part in pairing.
    """
    kind = getattr(part, "part_kind", "")
    if kind == "tool-return":
        return True
    return kind == "retry-prompt" and bool(getattr(part, "tool_name", None))


def align_to_pair_boundary(messages):
    """Repair tool_call <-> tool_return pairing and return a sequence safe to feed to the model.

    Repairs both ends: drop tool_returns / retries that can't find their tool_call (head),
    drop tool_calls that never got a tool_return (tail). Messages whose parts become empty are dropped entirely.
    None / empty / non-list input is returned as-is.
    """
    if not messages or not isinstance(messages, list):
        return messages

    # ① Head: a tool_return must find a tool_call with the same id **before** it
    seen_calls: set = set()
    forward = []
    for m in messages:
        parts = list(getattr(m, "parts", None) or [])
        if getattr(m, "kind", "") == "response":
            seen_calls.update(
                p.tool_call_id for p in parts if getattr(p, "part_kind", "") == "tool-call"
            )
            forward.append(m)
            continue
        kept = [p for p in parts
                if not _tool_linked(p) or getattr(p, "tool_call_id", None) in seen_calls]
        if len(kept) == len(parts):
            forward.append(m)
        elif kept:
            forward.append(_replace_parts(m, kept))
        # kept is empty -> drop the whole message (only orphans remain)

    # ② Tail: a tool_call must get a tool_return with the same id **after** it
    answered = {
        getattr(p, "tool_call_id", None)
        for m in forward if getattr(m, "kind", "") == "request"
        for p in (getattr(m, "parts", None) or []) if _tool_linked(p)
    }
    out = []
    for m in forward:
        if getattr(m, "kind", "") != "response":
            out.append(m)
            continue
        parts = list(getattr(m, "parts", None) or [])
        kept = [p for p in parts
                if getattr(p, "part_kind", "") != "tool-call" or p.tool_call_id in answered]
        if len(kept) == len(parts):
            out.append(m)
        elif kept:
            out.append(_replace_parts(m, kept))
        # kept is empty -> drop the whole message (only unanswered tool_calls remain)
    return out


def _replace_parts(message, parts):
    """Replace a message's parts, keeping other fields as-is. Falls back to the original object if dataclasses.replace fails."""
    import dataclasses

    try:
        return dataclasses.replace(message, parts=parts)
    except Exception:  # noqa: BLE001 — if it can't be replaced, don't; better keep a few extra messages than corrupt the structure
        return message


def use_instructions() -> bool:
    """The system prompt goes through `instructions=` rather than `system_prompt=`.

    The difference is that **instructions don't enter message_history**: pydantic-ai only attaches system_prompt
    when history is empty, so existing sessions forever use the prompt **as of persist time** — changed
    metric definitions and added rules never reach old sessions.

    ★ This switch comes **in pairs**: agent.py uses it to decide which parameter to pass, load_message_history uses it
      to decide whether to strip SystemPromptParts from history. **Both sides must take effect together**; stripping without switching leaves
      existing sessions with no system prompt at all (see the comment on strip_system_parts).
      So the criterion can live only here; don't add another env read on either side.
    """
    return (os.environ.get("AGENT_USE_INSTRUCTIONS", "1") or "").strip().lower() \
        in ("1", "true", "yes")


def strip_system_parts(messages):
    """Filter out SystemPromptParts from history; drop whole messages whose parts become empty.

    ★ Only call when AGENT_USE_INSTRUCTIONS=1. Stripping alone without switching to instructions= is an incident:
      pydantic-ai only attaches system_prompt when message_history is empty
      (`if not messages:` in `_agent_graph.py`), so after stripping, existing sessions end up with
      **no system prompt at all**, rather than "using the old one".
    """
    if not messages or not isinstance(messages, list):
        return messages
    out = []
    for m in messages:
        parts = list(getattr(m, "parts", None) or [])
        kept = [p for p in parts if getattr(p, "part_kind", "") != "system-prompt"]
        if len(kept) == len(parts):
            out.append(m)
        elif kept:
            out.append(_replace_parts(m, kept))
    return out


# ── History time cutoff ─────────────────────────────────────────────────
#
# agent_conversations has only one row per account and **never expires**. The 2026-08-27 session held
# 52 messages / 71KB from 6/4 -> 8/27, spanning three months, four brands (vigilath / <brand-c> / <brand-a>),
# two reports (#95 / #99), while _feed_cap is 50 messages — so the 8/25 "<brand-c> stuck at 5/8"
# context sat untouched on top of the 8/27 <brand-a> turn, and the user saw two brands' progress numbers
# mixed together in the same window; that was the "mess" they described.
#
# Message counts can't control this: ten messages a month still keeps the whole month in the window. It must be cut off by **time**.
# Consistent with the existing division of labor: only trim the part **fed to the model**; the DB still stores everything (save_message_history unchanged).


def _msg_time(m):
    """A message's time. ModelResponse carries its own timestamp; ModelRequest's time is on its parts."""
    ts = getattr(m, "timestamp", None)
    if ts is not None:
        return ts
    times = [t for t in (getattr(p, "timestamp", None)
                         for p in (getattr(m, "parts", None) or [])) if t is not None]
    return max(times) if times else None


def _feed_max_age_h() -> int:
    """How many hours back the history fed to the model may go. 0 = no time trimming (old behavior)."""
    try:
        return max(0, int(os.environ.get("AGENT_HISTORY_MAX_AGE_H", "24")))
    except (TypeError, ValueError):
        return 24


def drop_stale(messages, max_age_h: int, now=None):
    """Keep only the contiguous tail from **the last max_age_h hours**.

    Walk backward from the tail and stop at the first message that's definitely too old — guaranteeing a contiguous suffix of the original,
    with no holes in the middle (holes would split tool_call <-> tool_return, see align_to_pair_boundary).
    Messages without a timestamp stay with their adjacent newer messages and aren't judged on their own.
    """
    if not messages or not isinstance(messages, list) or max_age_h <= 0:
        return messages
    from datetime import datetime, timedelta, timezone

    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max_age_h)
    keep_from = 0
    for i in range(len(messages) - 1, -1, -1):
        ts = _msg_time(messages[i])
        if ts is not None:
            # Both kinds exist in the DB: tz-aware (newer pydantic-ai) and naive UTC (legacy)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts < cutoff:
                keep_from = i + 1
                break
    return messages[keep_from:]


def load_message_history(deps: AgentDeps):
    """Read the account × business line multi-turn history -> Pydantic AI message_history (None if none).

    After reading, pass it through align_to_pair_boundary first: existing sessions already broken by the old _truncate
    get repaired here on the fly, without waiting for the user to click reset.
    """
    row = _conv_query(deps).first()
    if not row or not row.messages_json or row.messages_json in ("[]", ""):
        return None
    try:
        from pydantic_ai.messages import ModelMessagesTypeAdapter
        history = ModelMessagesTypeAdapter.validate_json(row.messages_json)
    except Exception:  # noqa: BLE001 — corrupted history doesn't block the conversation
        return None
    if use_instructions():
        # Switching to instructions= requires also stripping old SystemPromptParts from history,
        # otherwise old sessions carry both "the old persisted prompt" and "the new instructions", two conflicting metric definitions
        history = strip_system_parts(history)
    try:
        # First cut off by time (stale context across days/tasks), then trim by count (volume within a turn), then align pairs.
        # The order can't be reversed: trimming by count first would let a day of chatter push out yesterday's truly relevant message.
        history = align_to_pair_boundary(drop_stale(history, _feed_max_age_h()))
        cap = _feed_cap(deps)
        if cap > 0 and len(history) > cap:
            # Trim on the **read side**, not persisted — a bad trim is fine on the next re-read, whereas the old save-side trim was irreversible
            history = align_to_pair_boundary(history[-cap:])
    except Exception:  # noqa: BLE001 — if it can't be repaired use it as-is; don't let the repair itself block the conversation
        pass
    return history or None


def _feed_cap(deps: AgentDeps) -> int:
    """Cap on the number of messages **fed to the model**. The admin line has bigger return bodies and more tools, so it's more aggressive.

    Same semantics as the old AGENT_HISTORY_CAP / AGENT_ADMIN_HISTORY_CAP, only moved from "at save time"
    to "at read time" — store everything, feed a trimmed slice. 0 = no trimming.
    This is a coarse **count-based** gate; a character-based context budget is a separate follow-up, after which this function becomes a fallback.
    """
    key = "AGENT_ADMIN_HISTORY_CAP" if deps.scope == "admin" else "AGENT_HISTORY_CAP"
    default = "20" if deps.scope == "admin" else "50"
    try:
        v = int(os.environ.get(key, default))
    except (TypeError, ValueError):
        v = 20 if deps.scope == "admin" else 50
    return 0 if v <= 0 else max(4, v)


def _history_hard_cap() -> int:
    """Hard cap on the **save** side, only to keep the table from blowing up; it doesn't do the "feed the model" trimming (that's the context budget's job).

    The old values were 50 / 20 messages; the admin line has 3–5 messages per turn, so it hit them in 4–6 turns, and hitting them broke pairs (see the
    comment on align_to_pair_boundary above). Raised to 400 and pairs are aligned after cutting. 0 = no trimming.
    """
    try:
        return max(0, int(os.environ.get("AGENT_HISTORY_HARD_CAP", "400")))
    except (TypeError, ValueError):
        return 400


def _truncate(messages_json: str, cap: int) -> str:
    """Keep only the last cap messages and repair pairs after cutting. cap<=0 means no trimming.

    ★ Trim from the **start** (keep the tail), rather than keeping the leading SystemPromptPart — that old system prompt
    is exactly what we don't want to carry over (Architecture-Current State §13). Hard constraints now go in per-turn user message prefix blocks.
    On trim failure always return as-is: better a long history than a corrupted one.
    """
    if cap <= 0:
        return messages_json
    try:
        import json

        arr = json.loads(messages_json)
        if not isinstance(arr, list) or len(arr) <= cap:
            return messages_json
        from pydantic_ai.messages import ModelMessagesTypeAdapter
        # Object-level round-trips are lossless (verified), so pairing repair needs just one implementation shared by save/read
        fixed = align_to_pair_boundary(ModelMessagesTypeAdapter.validate_python(arr[-cap:]))
        return ModelMessagesTypeAdapter.dump_json(fixed).decode()
    except Exception:  # noqa: BLE001
        return messages_json


def save_message_history(deps: AgentDeps, messages_json: bytes | str) -> None:
    """Save the account × business line multi-turn history. **Stores everything**, only bounded by AGENT_HISTORY_HARD_CAP's anti-blowup cap;
    "how much to feed the model" is decided on the read side (see load_message_history)."""
    from datetime import datetime

    from geo.models.agent import AgentConversationORM

    s = messages_json.decode() if isinstance(messages_json, (bytes, bytearray)) else str(messages_json)
    s = _truncate(s, _history_hard_cap())
    row = _conv_query(deps).first()
    if row is None:
        row = AgentConversationORM(account_id=deps.account_id, scope=(deps.scope or "self"))
        deps.db.add(row)
    row.messages_json = s
    row.updated_at = datetime.utcnow()
    deps.db.commit()


# ── Flow slot read/write ────────────────────────────────────────────────
#
# Keeping this in **a separate field** from message history is deliberate: messages_json is for the model and gets trimmed by time and count;
# context_json is for code and must not be trimmed. Once state like "industry terms still missing" mixes into message history,
# we're back to "letting the model remember state for us" — exactly what was lost in the 2026-08-27 <brand-a> session.


def load_flow(deps: AgentDeps) -> dict:
    """Read this account's flow slots for this business line. Unreadable / corrupt always returns {}, without blocking the conversation."""
    import json

    row = _conv_query(deps).first()
    if row is None:
        return {}
    try:
        ctx = json.loads(getattr(row, "context_json", None) or "{}")
    except Exception:  # noqa: BLE001
        return {}
    return ctx if isinstance(ctx, dict) else {}


def save_flow(deps: AgentDeps, ctx: dict) -> None:
    """Write back flow slots. Create the session row if it doesn't exist yet (slots may need saving on the very first turn)."""
    import json
    from datetime import datetime

    from geo.models.agent import AgentConversationORM

    row = _conv_query(deps).first()
    if row is None:
        row = AgentConversationORM(account_id=deps.account_id, scope=(deps.scope or "self"))
        deps.db.add(row)
    row.context_json = json.dumps(ctx or {}, ensure_ascii=False)
    row.updated_at = datetime.utcnow()
    deps.db.commit()


def reset_conversation(deps: AgentDeps) -> None:
    """Clear the current business line's conversation memory (for user "start over"; the other line's history is untouched).

    Deletes the whole row — context_json's slots get cleared along with it. Otherwise after "start over"
    the previous run's report_id would linger, and the next progress question would be answered about the old run.
    """
    _conv_query(deps).delete()
    deps.db.commit()


def usage_guardrail_check(deps: AgentDeps, action: str):
    """Unified call gate (non-billing) — **internal and external go through the same one**, decision + audit.

    The tier comes from deps.channel (internal web/admin/cron tiers have unlimited=1 and always pass),
    external embed/im/skill/api tiers are bound by daily counts, RPM, action and engine allowlists;
    both sides **write a call_audit row**, which is where "internal calls are also recorded in the external gateway" lands.

    Returns a Decision (with exec_mode / run_mode / priority); tools that need to decide
    "automation vs API, run now vs queue" read it directly; others can ignore it.
    Over limit raises CallDenied, mapped to HTTP (403/429) by the api layer and reported truthfully to the user in conversation.
    """
    from geo.services.call_policy import CallerRef, enforce

    caller = CallerRef(
        channel=deps.channel or "web",
        account_id=deps.account_id,
        user_id=deps.user_id,
        tenant_id=deps.tenant_id,
        tid=deps.tid or "",
        policy_key=deps.policy_key or "",
        request_id=deps.turn_id or "",
    )
    return enforce(deps.db, caller, action,
                   endpoint=f"agent:{deps.scope or 'self'}",
                   topic_id=deps.topic_id or deps.locked_topic_id)


def publish_execute(deps: AgentDeps, draft_ids: list[int]) -> None:
    """Template confirmed -> call publisher to publish + backfill publish_record. Triggered only by the "drafting completed event"; not callable by the model.

    TODO: wire up mediumsly_publisher etc.; verify the matching ExecutionPlan.status == confirmed.
    """
    raise NotImplementedError("publish_execute: W4 实现(模板确认后自动发文)")


def deliver_notification(deps: AgentDeps, message: str) -> None:
    """Thin wrapper kept for the old signature. New code should use `agents.geo.notify.push` directly."""
    from agents.geo.notify import push

    push(deps.db, deps.account_id, title="提醒", body=message,
         kind="generic", object_id=str(deps.topic_id or 0))
