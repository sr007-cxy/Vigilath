"""Sales Agent method layer — deterministic side effects, not directly exposed to the LLM."""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from agents.sales_agent.deps import SalesAgentDeps
from agents.sales_agent.models import SalesAgentChatMessageORM, SalesAgentChatSessionORM, SalesAgentUndoCheckpointORM
from geo.models.user import UserORM


_DEFAULT_SESSION_TITLE = "Sales Agent 会话"


def _dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


def _loads(s: str | None, default: Any = None) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        return default


def _message_title(content: str, max_len: int = 28) -> str:
    title = " ".join((content or "").split()).strip()
    if not title:
        return _DEFAULT_SESSION_TITLE
    return title[:max_len] + ("…" if len(title) > max_len else "")


def _session_key(session_id: str | None) -> str:
    key = str(session_id or "").strip()
    if not key:
        return ""
    try:
        return str(uuid.UUID(key))
    except ValueError as e:
        raise PermissionError("Sales Agent 会话 ID 无效") from e


def _find_user_session(db: Session, user_id: int, session_id: str | None) -> SalesAgentChatSessionORM | None:
    """Look up a session by the frontend public_id, always validating user_id."""
    key = _session_key(session_id)
    if not key:
        return None
    return (
        db.query(SalesAgentChatSessionORM)
        .filter(SalesAgentChatSessionORM.public_id == key, SalesAgentChatSessionORM.user_id == user_id)
        .first()
    )


def resolve_account(
    current_user: UserORM,
    db: Session,
    session_id: str | None = None,
    new_session: bool = False,
    page_context: dict | None = None,
) -> SalesAgentDeps:
    """Build the Sales Agent user context after authentication; the frontend uses public_id, internally the integer id is still used."""
    row: SalesAgentChatSessionORM | None = None
    if new_session:
        row = SalesAgentChatSessionORM(user_id=current_user.id, title=_DEFAULT_SESSION_TITLE)
        db.add(row)
        db.commit()
        db.refresh(row)
    elif _session_key(session_id):
        row = _find_user_session(db, current_user.id, session_id)
        if row is None:
            raise PermissionError("Sales Agent 会话不存在或不属于当前用户")
    if row is None:
        row = (
            db.query(SalesAgentChatSessionORM)
            .filter(SalesAgentChatSessionORM.user_id == current_user.id)
            .order_by(SalesAgentChatSessionORM.updated_at.desc(), SalesAgentChatSessionORM.id.desc())
            .first()
        )
    if row is None:
        row = SalesAgentChatSessionORM(user_id=current_user.id, title=_DEFAULT_SESSION_TITLE)
        db.add(row)
        db.commit()
        db.refresh(row)
    context = _loads(row.context_json, {}) or {}
    if not isinstance(context, dict):
        context = {}
    # Keep hand-off IDs between tools in the same session, so recommend_companies doesn't lose the profile after build_customer_profile.
    tool_state = context.get("tool_state") if isinstance(context.get("tool_state"), dict) else {}
    if page_context is not None:
        context["page_context"] = page_context
        context["tool_state"] = tool_state
        row.context_json = _dumps(context)
        row.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(row)
    return SalesAgentDeps(
        account_id=current_user.id,
        user_id=current_user.id,
        db=db,
        session_id=row.id,
        session_public_id=row.public_id,
        page_context=page_context,
        user_role=str(getattr(current_user, "role", None) or ("admin" if getattr(current_user, "is_admin", False) else "user")),
        tool_state=dict(tool_state),
    )


def load_message_history(deps: SalesAgentDeps):
    """Read Sales Agent session history -> Pydantic AI message_history."""
    if deps.session_id is None:
        return None
    row = deps.db.get(SalesAgentChatSessionORM, deps.session_id)
    if not row or row.user_id != deps.user_id or not row.messages_json or row.messages_json in ("[]", ""):
        return None
    try:
        from pydantic_ai.messages import ModelMessagesTypeAdapter
        history = ModelMessagesTypeAdapter.validate_json(row.messages_json)
    except Exception:  # noqa: BLE001
        return None
    return _trim_history_for_budget(deps, row, history)


def _trim_history_for_budget(deps: SalesAgentDeps, row: SalesAgentChatSessionORM, history):
    """Trim by budget before feeding the model. Storage is untouched — disabling the threshold restores the original behavior."""
    from agents.sales_agent.runtime.budget import budget_from_env, build_state_digest, estimate_chars, trim_history

    budget = budget_from_env()
    if not budget.enabled:
        return history
    before = estimate_chars(history)
    if before <= budget.max_chars:
        return history
    try:
        # lead_flow lives under context_json["tool_state"] (a symmetric convention between build_deps and
        # api._save_lead_flow_state), not at the top level. Reading the top level always returned None before,
        # so once history was trimmed the state digest lost "selected brand / current step" — the longer the conversation, the more
        # this summary is needed, yet that's exactly when it was empty, with no error throughout.
        _ctx = _loads(row.context_json, {}) or {}
        lead_flow = (_ctx.get("tool_state") or {}).get("lead_flow")
    except Exception:  # noqa: BLE001
        lead_flow = None
    digest = build_state_digest(deps.db, deps.user_id, lead_flow if isinstance(lead_flow, dict) else None)
    trimmed, did = trim_history(history, digest, budget)
    if did:
        deps.budget.update({"ctx_before": before, "ctx_after": estimate_chars(trimmed), "ctx_trimmed": True})
        try:
            from agents.sales_agent.runtime_log import write_sales_agent_log
            write_sales_agent_log({
                "event": "context_trim", "user_id": deps.user_id,
                "session_public_id": deps.session_public_id,
                "before_chars": before, "after_chars": estimate_chars(trimmed),
                "messages_before": len(history), "messages_after": len(trimmed),
            })
        except Exception:  # noqa: BLE001 — logging failures must not affect the conversation
            pass
    return trimmed


def _strip_tool_metadata(raw: str) -> str:
    """Strip the full card data on tool-returns from history.

    Tools in the recommendation chain split via `ToolReturn` into two parts: the model gets the summary, and the full card data hangs on `metadata`
    (see runtime/payload.py). The card itself is already in chat_messages.metadata_json; storing it again in history
    only bloats messages_json by hundreds of thousands of characters per recommendation — the model never sees it, yet the context budget
    is computed at that size, squeezing out the real conversation. On parse failure return as-is; saving history mustn't fail because of it.
    """
    try:
        messages = json.loads(raw)
    except (TypeError, ValueError):
        return raw
    if not isinstance(messages, list):
        return raw
    touched = False
    for message in messages:
        for part in (message.get("parts") or []) if isinstance(message, dict) else []:
            if isinstance(part, dict) and part.get("part_kind") == "tool-return" and part.get("metadata") is not None:
                part["metadata"] = None
                touched = True
    return json.dumps(messages, ensure_ascii=False) if touched else raw


def save_message_history(deps: SalesAgentDeps, messages_json: bytes | str) -> None:
    """Save Pydantic AI multi-turn history."""
    if deps.session_id is None:
        return
    s = messages_json.decode() if isinstance(messages_json, (bytes, bytearray)) else str(messages_json)
    s = _strip_tool_metadata(s)
    row = deps.db.get(SalesAgentChatSessionORM, deps.session_id)
    if row is None or row.user_id != deps.user_id:
        return
    context = _loads(row.context_json, {}) or {}
    if not isinstance(context, dict):
        context = {}
    # Write tool hand-off state back to session.context_json, supporting continuation of the recommendation flow across turns.
    context["tool_state"] = deps.tool_state or {}
    row.messages_json = s
    row.context_json = _dumps(context)
    row.updated_at = datetime.utcnow()
    deps.db.commit()


def _should_retitle(current: str, content: str) -> bool:
    """While the title isn't settled, keep following the latest sentence.

    It used to take only the first user message, so sessions starting with "hello?" were named hello? forever — in production a screen of a dozen-plus
    were all hello?, impossible to tell apart. Now: if this sentence has content and the current title is the default or still
    a greeting/chit-chat, switch to this sentence.
    """
    from agents.sales_agent.agent import should_use_guide_reply

    text = str(content or "").strip()
    if not text or should_use_guide_reply(text):
        return False
    title = str(current or "").strip()
    return not title or title == _DEFAULT_SESSION_TITLE or should_use_guide_reply(title)


def append_chat_message(
    deps: SalesAgentDeps,
    role: str,
    content: str,
    intent: str = "",
    tool_name: str = "",
    tool_result_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    if deps.session_id is None:
        return
    sess = deps.db.get(SalesAgentChatSessionORM, deps.session_id)
    if sess is not None and sess.user_id == deps.user_id:
        if role == "user" and _should_retitle(sess.title, content):
            sess.title = _message_title(content)
        sess.updated_at = datetime.utcnow()
    row = SalesAgentChatMessageORM(
        session_id=deps.session_id,
        role=role,
        content=content or "",
        intent=intent or "",
        tool_name=tool_name or "",
        tool_result_id=tool_result_id or "",
        metadata_json=_dumps(metadata or {}),
    )
    deps.db.add(row)
    deps.db.commit()


def reset_conversation(deps: SalesAgentDeps) -> None:
    """Clear the current user's Sales Agent session memory and chat details."""
    q = deps.db.query(SalesAgentChatSessionORM).filter(SalesAgentChatSessionORM.user_id == deps.user_id)
    if deps.session_id is not None:
        q = q.filter(SalesAgentChatSessionORM.id == deps.session_id)
    sessions = q.all()
    ids = [s.id for s in sessions]
    if ids:
        deps.db.query(SalesAgentUndoCheckpointORM).filter(SalesAgentUndoCheckpointORM.session_id.in_(ids)).delete(synchronize_session=False)
        deps.db.query(SalesAgentChatMessageORM).filter(SalesAgentChatMessageORM.session_id.in_(ids)).delete(synchronize_session=False)
        for s in sessions:
            s.messages_json = "[]"
            s.context_json = "{}"
            s.current_intent = ""
            s.updated_at = datetime.utcnow()
    deps.db.commit()


def usage_guardrail_check(deps: SalesAgentDeps, action: str, seller_profile_id: int | None = None) -> None:
    """Pre-action gate. The call sites of the 11 tools are unchanged; rules are in runtime/guard.py.

    `seller_profile_id` is only used by report actions (judging report capability per brand); for other actions passing it or not makes no difference.
    """
    from agents.sales_agent.runtime.guard import check

    check(deps, action, seller_profile_id)
