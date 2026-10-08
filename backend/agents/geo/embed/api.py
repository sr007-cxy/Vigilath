"""/api/agent/v1/* — stable external surface (for external agents / Xiaolongxia to connect).

Auth: Authorization: Bearer <1-year embed token> (not a login JWT).
account_id is always decoded from the token and injected by the backend; caps narrowed; publish never reachable.
"""
from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from agents.geo.deps import AgentDeps
from agents.geo.embed.tokens import EmbedAuthError, EmbedClaims, verify_token
from agents.geo.methods import (
    load_message_history,
    reset_conversation,
    save_message_history,
)
from geo.database import SessionLocal

log = logging.getLogger(__name__)

# CARD_TOOLS is **shared** with the internal channel (agents/geo/cards.py).
# This file once had its own copy that missed run_full_diagnosis — so when the industry term was missing, the external channel
# got no pending card and fell back to "the model paraphrasing a dict in text", exactly what the pending card is meant to eliminate.
from agents.geo.cards import cards_from  # noqa: E402

router = APIRouter(prefix="/agent/v1")

# /data/{name} -> read-only tools (bypass the LLM, fetch data directly)
# ★ Old names (today/growth/batch/report) are **kept as aliases**: the external embed API may already be integrated by customers,
#   and removing keys would 404 them. After the redesign they all point to the new report-center tools.
DATA_TOOLS = {
    "overview": "get_report_overview",
    "period": "get_period_report",
    "periods": "list_report_periods",
    "coverage": "get_query_coverage",
    "publish": "get_publish_status",
    "diagnosis": "get_full_diagnosis",
    "run": "get_run_status",
    # Compatibility with old names
    "today": "get_report_overview",
    "growth": "get_report_overview",
    "batch": "get_run_status",
    "report": "get_full_diagnosis",
}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def embed_claims(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> EmbedClaims:
    """Parse the Bearer embed token -> EmbedClaims. Failures map to HTTP errors."""
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    try:
        return verify_token(db, token)
    except EmbedAuthError as e:
        raise HTTPException(status_code=e.code, detail=e.detail)


def _channel_of(client: str) -> str:
    """External channel breakdown: the skill client self-reports `X-Vigilath-Client: skill` (see geo_client.py under skills/),
    everything else is embed. Split only so tiers can be configured separately — auth and account resolution are identical."""
    return "skill" if (client or "").strip().lower() == "skill" else "embed"


def _deps(claims: EmbedClaims, db: Session, channel: str = "embed") -> AgentDeps:
    return AgentDeps(
        account_id=claims.account_id, user_id=claims.account_id, db=db, caps=claims.caps,
        # External channel: the skill client self-reports X-Vigilath-Client: skill so it can be tightened separately; the rest counts as embed
        channel=channel or "embed",
        tid=claims.tid,
        policy_key=claims.policy_key,
        # One id per request: gate audit (call_audit.request_id) and usage records rely on it to map "the reference the user reports"
        # back to a specific call; without it, external audit rows all have empty request_id and incidents can't be traced.
        turn_id=uuid.uuid4().hex,
    )


class ChatIn(BaseModel):
    message: str


class _FakeCtx:
    """Read-only tools only use ctx.deps; build a minimal ctx and call the tool directly, bypassing the LLM."""

    def __init__(self, deps: AgentDeps):
        self.deps = deps


def _embed_preamble(deps) -> str:
    """Per-turn hard constraints for the external channel.

    This path **previously had no preamble at all** — the internal web line's metric definitions relied entirely on system_prompt,
    and system_prompt gets serialized into history, so existing sessions use the version at persist time. The external channel has the
    most users and longest sessions, yet was the least looked after.

    Externally only two rules: capability boundary (so the model doesn't promise abilities it can't call, inviting follow-ups) + degradation discipline.
    """
    return (
        "【能力边界】当前通道**不能**改提示词、不能发布内容、不能付费投放、不能动用账户余额。"
        "用户要做这些时,直接告诉他到网页端操作,别说「我帮你发」再被拦下。\n"
        "【降级】返回体里 data_quality 不是 full 时,如实说这次是降级数据、缺了哪几项;"
        "绝不根据公网检索结果反推露出率/命中率/评分。\n"
    )


@router.post("/chat")
async def chat(body: ChatIn, claims: EmbedClaims = Depends(embed_claims), db: Session = Depends(get_db),
               x_vigilath_client: str = Header(default="")):
    """Chat (SSE). Reuses the internal agent.run() full tool loop; toolset narrowed by caps, never includes publish."""
    from agents.geo.agent import build_embed_agent, route_line

    line = route_line(body.message)      # Keep the business line in a variable: profile_for in event_stream below needs it
    channel = _channel_of(x_vigilath_client)
    # Pass channel all the way through — external channels can't get money-spending / media-writing tools (gates.py + build_embed_agent's
    # code-level permanent exclusion, two layers)
    agent = build_embed_agent("write" in claims.caps, line=line, channel=channel)
    deps = _deps(claims, db, channel)
    history = load_message_history(deps)

    _t0 = time.perf_counter()

    async def event_stream():
        from agents.geo.api import _record_agent_usage
        from agents.geo.runtime.limits import profile_for, public_message, run_guarded, usage_limits_for

        _p = profile_for('chat', line, channel='api')
        try:
            result = await run_guarded(
                agent.run(_embed_preamble(deps) + body.message,
                          deps=deps, message_history=history,
                          usage_limits=usage_limits_for(_p)),
                profile=_p)
            text = result.output or ""
            for i in range(0, len(text), 24):
                yield f"data: {json.dumps({'delta': text[i:i + 24]}, ensure_ascii=False)}\n\n"
            # Only this turn: all_messages() includes message_history and would re-push historical cards every turn
            cards = cards_from(result)
            if cards:
                yield f"data: {json.dumps({'cards': cards}, ensure_ascii=False, default=str)}\n\n"
            try:
                save_message_history(deps, result.all_messages_json())
            except Exception:  # noqa: BLE001
                pass
            _record_agent_usage(result, scene="chat", scope=deps.channel, deps=deps, t0=_t0)
            yield "data: {\"done\": true}\n\n"
        except Exception as e:  # noqa: BLE001
            # External channel: **no exception text may ever be shown to the user**.
            #   - timeout / step limit / gate hit -> public_message is human-readable, output as body text, not an error;
            #   - everything else (code bugs, provider glitches) -> one human sentence + a reference id; the raw text only goes to server logs.
            # Counterexample from just yesterday: embed stuffed a NameError verbatim into the error event, the skill client
            # called _die and printed red text, and the user saw "name 'line' is not defined".
            friendly = public_message(e)
            status = "usage_limit" if friendly else "http_error"
            if not friendly:
                # The reference id is the first 8 chars of turn_id — same source as call_audit.request_id / the usage record's ref_id,
                # so a reference reported by the user locates that exact call
                ref = (deps.turn_id or uuid.uuid4().hex)[:8]
                log.exception("[embed/chat] unexpected exception ref=%s channel=%s tid=%s",
                              ref, deps.channel, claims.tid)
                friendly = (f"这次没能完成,已经记录下来了(编号 {ref})。"
                            f"可以换个说法再问一次;还是不行就把这个编号发给我们。")
            _record_agent_usage(None, scene="chat", scope=deps.channel, deps=deps, t0=_t0,
                                status=status, error=e)
            yield f"data: {json.dumps({'delta': friendly}, ensure_ascii=False)}\n\n"
            yield "data: {\"done\": true}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/reset")
async def reset(claims: EmbedClaims = Depends(embed_claims), db: Session = Depends(get_db)):
    reset_conversation(_deps(claims, db))
    return {"ok": True}


@router.get("/data/{name}")
async def data(name: str, claims: EmbedClaims = Depends(embed_claims), db: Session = Depends(get_db)):
    """Read-only data fetch, bypassing the LLM and calling the matching read-only tool directly."""
    tool_name = DATA_TOOLS.get(name)
    if not tool_name:
        raise HTTPException(status_code=404, detail=f"unknown data: {name}; 可选 {sorted(DATA_TOOLS)}")
    from pydantic_ai import ModelRetry

    from agents.geo import tools as t

    fn = getattr(t, tool_name)
    try:
        return await fn(_FakeCtx(_deps(claims, db)))
    except ModelRetry as e:
        # Tools express "precondition not met" (e.g. the account has no topic yet) via ModelRetry, meant for the agent loop to self-correct;
        # in direct data fetches nobody catches it, and a bare raise becomes a 500. Convert to a structured need_setup so the caller can relay it to the user.
        return {"status": "need_setup", "hint": str(e)}


@router.get("/meta/capabilities")
async def capabilities(claims: EmbedClaims = Depends(embed_claims)):
    return {
        "account_id": claims.account_id,
        "caps": claims.caps,
        "can_write": "write" in claims.caps,
        "can_publish": False,            # Real external publishing is never open externally (controlled by internal platform guardrails)
        "data": sorted(DATA_TOOLS),
    }
