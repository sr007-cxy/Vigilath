"""/api/agent/* routes. pydantic-ai is not imported at module level (imported lazily inside endpoints), so it loads even when not installed, without affecting the main app.

Registration (in geo/main.py):
    from agents.geo import api as agent_api
    app.include_router(agent_api.router, prefix="/api", tags=["agent"])
"""
from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from agents.geo.auth import get_current_user          # Independent validation, does not import geo.api
from agents.geo.cards import (
    CARD_TOOLS,
    cards_from,
    confirms_from,
    tool_returns_of_turn,
    ui_actions_from,
)
from agents.geo.methods import (
    load_flow,
    load_message_history,
    reset_conversation,
    resolve_account,
    save_flow,
    save_message_history,
)
from geo.database import SessionLocal
from geo.models.user import UserORM

log = logging.getLogger(__name__)

# CARD_TOOLS moved to agents/geo/cards.py — internal and external embed channels must share one copy.
# The name is kept here so existing imports don't break (tests/geo_agent and embed both imported it from here).
__all__ = ["CARD_TOOLS", "router"]

router = APIRouter(prefix="/agent")


def _record_agent_usage(result, *, scene: str, scope: str, deps, t0: float,
                        status: str = "ok", error: BaseException | None = None) -> None:
    """Record one agent conversation turn into model usage.

    Metric definition: **one row per turn**, requests_count = result.usage().requests — pydantic-ai's
    RunUsage already aggregates the multiple "model -> tool -> model" round trips in the turn. The page's "call count"
    uses SUM(requests_count) throughout, so one row representing N calls doesn't undercount the total.
    On failure usage is unavailable, so record once (count is at least 1).
    """
    try:
        import time as _time

        from agents.geo.model import describe_model
        from geo.services.llm_usage import record_llm_call

        provider, model, base_url = describe_model(scene)
        usage = None
        if result is not None:
            try:
                usage = result.usage()
            except Exception:  # noqa: BLE001
                usage = None
        record_llm_call(
            service="agent", feature=f"agent.{scope or 'self'}",
            provider=provider or "unknown", model=model, endpoint=base_url,
            usage=usage, status=status,
            error_kind=type(error).__name__[:24] if error is not None else "",
            latency_ms=int((_time.perf_counter() - t0) * 1000),
            user_id=getattr(deps, "user_id", None),
            topic_id=getattr(deps, "topic_id", None) or getattr(deps, "locked_topic_id", None),
            ref_type="session", ref_id=getattr(deps, "turn_id", "") or "",
            trace_id=getattr(deps, "turn_id", "") or "",
            extra={"error": str(error)[:200]} if error is not None else None,
        )
    except Exception:  # noqa: BLE001 — accounting must never affect the conversation
        pass



def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class ChatIn(BaseModel):
    message: str
    topic_id: int | None = None
    page_context: dict | None = None   # Frontend "current page": {page, describe, snapshot, actions[], route}; used by control_page to operate the UI
    scope: str | None = None           # "admin" = request the ops workspace line; missing/other = customer self-service line (self)


def _page_preamble(pc: dict | None) -> str:
    """Assemble the frontend page context into a 【页面上下文】 ("page context") note for the model, prepended to this turn's user message."""
    if not pc:
        return ""
    actions = [str(a) for a in (pc.get("actions") or [])]
    if not actions:
        return ""
    import json as _json
    # Distinguish "on an actionable page" from "just a regular page (navigation only)"; don't bluntly say unknown in the copy
    page_actions = [a for a in actions if a != "navigate"]
    describe = pc.get("describe") or pc.get("page")
    if describe and page_actions:
        lines = [f"【页面上下文】用户当前在「{describe}」,你可替他操作此页面。"]
    else:
        lines = ["【页面上下文】用户当前页面没有可填写/可调整的项,但你可以帮他跳转到其他页面。"]
    lines.append(f"可用动作:{', '.join(actions)}。")
    snap = pc.get("snapshot")
    if snap:
        lines.append(f"当前页面数据(表单/筛选值):{_json.dumps(snap, ensure_ascii=False)}。")
    navs = pc.get("nav_targets") or []
    if navs:
        catalog = "、".join(f"{n.get('label')}={n.get('path')}" for n in navs if n.get("path"))
        lines.append(f"navigate 可跳转的站内页面(动作名 navigate,args 传 {{path}}):{catalog}。")
    lines.append("如需替用户操作页面,调用 control_page;action 只能取上面列出的可用动作。\n")
    return "\n".join(lines)


def _self_preamble(deps) -> str:
    """Per-turn hard constraints for the customer line — same reason as `_admin_preamble`: **put them in the user message, not the system prompt**.

    system_prompt gets serialized into session history, so existing sessions forever use the version at persist time — metric definition changes
    don't take effect for old sessions. The ops console worked around this long ago with per-turn prefix blocks; the customer line never got the patch, so the
    hard rules added on 2026-08-25 like "three spending essentials" and "degradation discipline" had no effect on any existing session.

    Only the three most critical rules. The longer the prefix block, the more tokens it uses and the more likely the model skips it as background noise.
    """
    from agents.geo.tools_billing import TOPUP_HINT

    bits = [
        "【花钱】要扣钱的动作(付费投放、产稿、舆情快检、跑批、开通日常监控)一律"
        "**两步**:第一次调用会拿到 needs_confirm 预览,把 preview / impact / "
        "irreversible / cost 逐条如实转述给用户,问一句「确认执行吗」,**然后本轮就结束**。"
        "用户下一条明确确认后,才用完全相同的参数 + 返回的 confirm_token 再调一次。"
        "绝不在同一轮里自己确认,绝不编造令牌,没真执行绝不说「已完成 / 已扣费」。"
        f"余额不够就直接说差多少、{TOPUP_HINT},别绕。",
        "【降级】返回体里 data_quality 不是 full 时,如实说这次是降级数据、缺了哪几项;"
        "**绝不**根据公网检索结果反推露出率/命中率/评分这类必须靠真实跑批才算得出的数。",
        "【别编】积分明细必须来自 list_ledger,跨期比较必须来自真实取到的两期数据;"
        "没查过就说没查,**绝不**凭余额差额或印象编一份出来。",
    ]
    if not deps.topic_id:
        bits.append("【主题】当前没有选定主题。要花钱的动作之前,先用 list_topics "
                    "列出来问用户选哪一个,不要自己挑。")
    return "".join(f"{b}\n" for b in bits)


def _admin_preamble(deps, topic) -> str:
    """Per-turn hard constraints for the ops console — **put into the user message, not the system prompt**.

    Known gap (Architecture-Current State §13): system_prompt gets serialized into session history; existing sessions forever use the persisted version,
    so metric definition changes don't reach old sessions. Sessions are already isolated by scope (see methods._conv_query), but that only solves
    "customer-line old prompts leaking into the ops console"; once the ops console's own history is persisted, later ADMIN_SYSTEM_PROMPT edits also don't apply.
    So the two most critical rules (project lock + two-step confirmation) are re-injected here every turn — the user message is written fresh each turn, unpolluted by history.
    """
    if topic is None:
        return ""
    return (
        f"【运营台上下文】你正在运营工作台里操作项目「{topic.name}」(项目 id={topic.id},"
        f"客户 user_id={topic.user_id})。本轮你**只能操作这一个项目**,没有任何工具能换项目;"
        f"用户要换项目时,请他在工作台打开那个项目的编辑页。\n"
        f"【两步确认】带 confirm_token 的工具:第一次留空拿预览 → 把预览如实转述给用户并**结束本轮** → "
        f"用户下一条明确确认后,用相同参数 + 返回的 confirm_token 再调一次。"
        f"绝不在同一轮里自己确认,绝不编造令牌,没真执行绝不说「已完成」。\n"
    )


# Tool -> one human-readable sentence for the user. Used for SSE stage events.
#
# Why: heavy tasks like the one-click full diagnosis take tens of seconds to minutes per turn, during which the chat window only shows
# 「思考中…」 ("Thinking..."). Users can't tell whether it's working or stuck, so they keep asking —
# in that 2026-08-27 session the user asked five times in a row "is it done / so slow / what's going on".
# Unregistered tools don't push a stage (better say nothing than say something wrong).
_STAGE_LABELS = {
    "run_full_diagnosis": "正在发起全面诊断…",
    "get_full_diagnosis": "正在查诊断进度…",
    "run_sentiment_once": "正在跑舆情快检…",
    "get_sentiment_once": "正在取舆情快检结果…",
    "get_sentiment_today": "正在查今日舆情…",
    "get_sentiment_history": "正在查历史舆情…",
    "get_hot_topics": "正在拉热点榜…",
    "websearch_evidence": "正在公网检索取证…",
    "ingest_material": "正在抓取并解析资料…",
    "draft_articles": "正在产稿…",
    "expand_prompts": "正在扩展候选提示词…",
    "get_report_overview": "正在取报告中心数据…",
    "get_period_report": "正在取期次报告…",
    "get_query_coverage": "正在统计命中情况…",
    "get_publish_status": "正在查发布进度…",
    "search_media": "正在检索媒体资源…",
    "auto_select_media": "正在选媒…",
}


def _stage_of(node) -> str:
    """Which tools this step is calling -> one human-readable sentence. Returns an empty string (no event) if it can't be composed."""
    resp = getattr(node, "model_response", None)
    names = [getattr(p, "tool_name", "") for p in (getattr(resp, "parts", None) or [])
             if getattr(p, "part_kind", "") == "tool-call"]
    hits = [_STAGE_LABELS[n] for n in dict.fromkeys(names) if n in _STAGE_LABELS]
    return hits[0] if len(hits) == 1 else ("、".join(h.rstrip("…") for h in hits) + "…" if hits else "")


def _persist_partial_history(deps, run) -> None:
    """On timeout interruption, save the history produced so far (fixing trailing orphan tool_calls).

    The whole thing is wrapped in try/except: if persisting fails, fall back to the old behavior (don't save this turn), never break
    this turn's response for the sake of "saving a bit more". AGENT_SAVE_ON_TIMEOUT=0 reverts to the old behavior in one switch.
    """
    import os

    if (os.environ.get("AGENT_SAVE_ON_TIMEOUT", "1") or "").strip().lower() \
            in ("0", "false", "no"):
        return
    try:
        from pydantic_ai.messages import ModelMessagesTypeAdapter

        from agents.geo.methods import align_to_pair_boundary, save_message_history

        msgs = list(run.ctx.state.message_history or [])
        aligned = align_to_pair_boundary(msgs)
        if not aligned:
            return
        save_message_history(
            deps, ModelMessagesTypeAdapter.dump_json(aligned))
    except Exception:  # noqa: BLE001 — if it can't be saved treat it as not saved, old behavior
        log.warning("[agent] failed to persist on timeout; discarding this turn's history", exc_info=True)


def _load_agent(scene: str = "chat", line: str = "both", channel: str = "web"):
    try:
        from agents.geo.agent import build_agent
        # channel decides whether money-spending / media-writing tools are loaded (see gates.py); it must be passed all the way through.
        # This path is our own web app and ops console, so it defaults to web.
        return build_agent(scene, line, channel)
    except Exception as e:  # pydantic-ai not installed / DEEPSEEK_API_KEY missing
        raise HTTPException(status_code=503, detail=f"Agent 暂不可用:{e}")


@router.post("/chat")
async def chat(
    body: ChatIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Chat (SSE streaming). Session unit = account × business line; multi-turn memory persisted to DB + data tool results pushed to the frontend as structured cards."""
    import uuid

    from agents.geo.agent import route_line
    from agents.geo.scope import resolve_scope

    # Business line: the ops console is determined by role + explicit scope (**not keywords** — ops and customer text is indistinguishable,
    # and a misjudgment costs "executing writes on a customer project"); the customer line still routes by message keywords to GEO / sentiment toolsets.
    from agents.geo.flow import DiagnosisState, absorb as absorb_flow, extract as extract_slots
    from agents.geo.flow.router import blocks_other_lines, preamble as flow_preamble

    scope = resolve_scope(current_user, requested_scope=body.scope,
                          topic_id=body.topic_id, page_context=body.page_context)
    deps = resolve_account(current_user, db, topic_id=body.topic_id, page_context=body.page_context,
                           scope=scope, turn_id=uuid.uuid4().hex, user_message=body.message)

    # ── Flow slots: first merge in slots that can be confidently recognized in this message ──────────
    # The admin line doesn't use this (the ops console operates on customers' existing projects; there's no "gather params then start" step).
    flow_ctx = {} if scope == "admin" else load_flow(deps)
    state = DiagnosisState.from_ctx(flow_ctx)
    # ★ The line-lock criterion must use the state **before merging**. This message itself is the answer to the previous pending card —
    #   after merging next_step becomes ready and the lock releases automatically, yet this turn is **exactly** the one that most needs
    #   run_full_diagnosis present (「行业词:… 口碑」 ("industry term: ... reputation") gets judged as sentiment by route_line).
    lock_geo = scope != "admin" and blocks_other_lines(state)
    if scope != "admin":
        if state.merge(extract_slots(body.message)):
            save_flow(deps, state.to_ctx(flow_ctx))

    # ── Business line ─────────────────────────────────────────────────────
    # ★ While waiting for the user to fill a slot, **lock to the geo line** regardless of this message's keywords.
    #   route_line judges message by message, and the user's industry term answer may well contain sentiment words
    #   (「口碑营销」「风险」「差评」 ("reputation marketing", "risk", "bad reviews") are all in _SENTIMENT_KW) — once routed to the sentiment line,
    #   this turn can't even load run_full_diagnosis' schema and the diagnosis never gets started.
    if scope == "admin":
        line = "admin"
    elif lock_geo or blocks_other_lines(state):
        line = "geo"
    else:
        line = route_line(body.message)
    agent = _load_agent("chat", line, "admin" if scope == "admin" else "web")
    history = load_message_history(deps)   # Multi-turn: load this account's history for this business line

    preamble = _page_preamble(body.page_context)
    if scope == "admin":
        topic = None
        if deps.locked_topic_id:
            from geo.models.ai_telemetry import AiTelemetryTopicORM
            topic = db.get(AiTelemetryTopicORM, deps.locked_topic_id)
        preamble = _admin_preamble(deps, topic) + preamble
    else:
        # 【当前流程】 ("current flow") goes first: it's this turn's hardest constraint ("don't send a single byte before getting the industry term"),
        # and the further back a prefix block is, the more likely it gets skipped as background noise.
        preamble = flow_preamble(state) + _self_preamble(deps) + preamble
    run_message = preamble + body.message   # Prepend notes when page/ops console context is present

    from agents.geo.runtime.limits import (
        deadline_for, profile_for, public_message, usage_limits_for,
    )
    profile = profile_for("chat", line)
    _t0 = time.perf_counter()

    async def event_stream():
        try:
            # Use agent.iter() to walk the full graph (model -> tool -> model, tools execute normally), **streaming tokens as they are generated**.
            # Can't use run_stream()+stream_text(): when DeepSeek emits text+tool_call in one response it treats the text as final and
            # skips tools (cards all 0/0). iter() executes node by node, tools still run, and each model node can stream tokens.
            from pydantic_ai import Agent
            from pydantic_ai.messages import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta

            from agents.geo.runtime.limits import GeoAgentTimeout

            # Whole-turn wall-clock cap: run_guarded can't wrap an async generator, so compare manually in the node loop.
            # (prod is Python 3.10, which lacks the asyncio.timeout context manager.)
            deadline = deadline_for(profile)
            async with agent.iter(run_message, deps=deps, message_history=history,
                                  usage_limits=usage_limits_for(profile)) as run:
                async for node in run:
                    if time.perf_counter() > deadline:
                        # Timeouts used to **not persist the turn at all**: run.result is None so
                        # all_messages_json() is unavailable, and this turn contains "tool_calls sent without a
                        # tool_return"; saving them makes pydantic-ai raise on the next turn
                        # UserError(unprocessed tool calls).
                        #
                        # But that conclusion was too heavy-handed — it lost not just this turn but **all history before it**,
                        # and users felt "it forgot everything mid-conversation". Only the trailing orphan tool_calls really need dropping,
                        # which is exactly what align_to_pair_boundary does (its comment says explicitly
                        # "a timed-out turn has exactly this shape"; it just was never called here).
                        _persist_partial_history(deps, run)
                        _record_agent_usage(None, scene="chat", scope=scope, deps=deps, t0=_t0,
                                            status="timeout", error=GeoAgentTimeout(profile))
                        yield f"data: {json.dumps({'delta': public_message(GeoAgentTimeout(profile))}, ensure_ascii=False)}\n\n"
                        yield 'data: {"done": true}\n\n'
                        return
                    if not Agent.is_model_request_node(node):
                        # Tool node: push "what it's doing" to the frontend, so long turns don't just show 「思考中…」 ("Thinking...").
                        # is_call_tools_node may not exist on older versions — skip silently if unavailable, without affecting the main path.
                        try:
                            if getattr(Agent, "is_call_tools_node", None) and Agent.is_call_tools_node(node):
                                _stage = _stage_of(node)
                                if _stage:
                                    yield f"data: {json.dumps({'stage': _stage}, ensure_ascii=False)}\n\n"
                        except Exception:  # noqa: BLE001 — a progress hint isn't worth failing a turn
                            pass
                        continue
                    async with node.stream(run.ctx) as req_stream:
                        async for ev in req_stream:
                            delta = None
                            if isinstance(ev, PartStartEvent) and isinstance(ev.part, TextPart):
                                delta = ev.part.content
                            elif isinstance(ev, PartDeltaEvent) and isinstance(ev.delta, TextPartDelta):
                                delta = ev.delta.content_delta
                            if delta:
                                yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
                result = run.result

            # Extract returns from data tools actually executed this turn -> structured cards / page actions / confirmation previews.
            # ★ All three only look at **this turn's new messages** (cards.turn_messages). Using all_messages() would re-push
            #   historical cards, control_page directives and confirmation cards every turn, and the frontend executes them all —
            #   the page navigates on its own and old cards flood the screen. That was the 2026-08-27 "messy stuff".
            cards = cards_from(result)
            if cards:
                yield f"data: {json.dumps({'cards': cards}, ensure_ascii=False, default=str)}\n\n"

            ui_actions = ui_actions_from(result)
            if ui_actions:
                yield f"data: {json.dumps({'ui_actions': ui_actions}, ensure_ascii=False, default=str)}\n\n"

            # Clicking 「确认执行」 ("Confirm") = sending a **regular message** into the conversation (no new endpoint, no token round-trip);
            # the model takes the token from its own history — exactly the same path as the user typing 「确认」 ("confirm"),
            # so the SAME_TURN check holds naturally (button click = new HTTP request = new turn_id).
            confirms = confirms_from(result)
            if confirms:
                yield f"data: {json.dumps({'confirms': confirms}, ensure_ascii=False, default=str)}\n\n"

            # Absorb what the tools **actually did** this turn into slots (which run was started / still waiting for a term).
            # Read tool returns rather than the model's paraphrase — the model misremembers brands; slots only trust facts.
            if scope != "admin":
                try:
                    if absorb_flow(state, tool_returns_of_turn(result)):
                        save_flow(deps, state.to_ctx(flow_ctx))
                except Exception:  # noqa: BLE001 — failing to save slots shouldn't affect this turn's answer
                    log.warning("[agent] failed to persist flow slots", exc_info=True)

            # Persist multi-turn memory to DB
            try:
                save_message_history(deps, result.all_messages_json())
            except Exception:  # noqa: BLE001 — failing to save history doesn't affect this turn's answer
                pass
            _record_agent_usage(result, scene="chat", scope=scope, deps=deps, t0=_t0)
            yield "data: {\"done\": true}\n\n"
        except Exception as e:  # noqa: BLE001
            # Record failures too, otherwise agent errors are completely invisible on the "model usage" page
            friendly = public_message(e)
            _record_agent_usage(None, scene="chat", scope=scope, deps=deps, t0=_t0,
                                status=("usage_limit" if friendly else "http_error"), error=e)
            if friendly:      # Over limit: a readable conclusion, not a fault; output as body text, don't render as a red error
                yield f"data: {json.dumps({'delta': friendly}, ensure_ascii=False)}\n\n"
                yield 'data: {"done": true}\n\n'
            else:
                yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/reset")
async def reset(
    scope: str | None = None,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Clear conversation memory (user "start over").

    Sessions are partitioned by "account × business line", so say which line to clear:
    scope="admin" clears the ops console one, omitted = the customer self-service one, scope="all" clears both.
    """
    which = (scope or "").strip()
    if which == "all":
        for s in ("self", "admin"):
            reset_conversation(resolve_account(current_user, db, scope=s))
        return {"ok": True, "scope": "all"}
    s = "admin" if which == "admin" else "self"
    reset_conversation(resolve_account(current_user, db, scope=s))
    return {"ok": True, "scope": s}


@router.get("/history")
async def history(
    scope: str | None = None,
    limit: int = 20,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Replay the last few turns (text + cards) to backfill the window when the frontend mounts.

    The frontend's msgs is pure component state: a refresh or route change wipes the whole conversation — while the backend kept it all along.
    Users felt "I just discussed the diagnosis with it, refreshed, and it remembers nothing", so they re-asked and started another run.

    Only replay **user-visible** content: the user-prompt's original text (prefix blocks stripped), the assistant's body,
    and tool returns hitting CARD_TOOLS. Don't replay tool_call / retry / system — those are internal mechanics
    that look like garbage to users (half of the "messy stuff" the user mentioned on 2026-08-27 was this leaking through).
    """
    import json as _json

    from geo.models.agent import AgentConversationORM

    which = "admin" if (scope or "").strip() == "admin" else "self"
    row = (db.query(AgentConversationORM)
           .filter(AgentConversationORM.account_id == current_user.id,
                   AgentConversationORM.scope == which)
           .first())
    if row is None or not row.messages_json:
        return {"messages": []}
    try:
        raw = _json.loads(row.messages_json)
    except Exception:  # noqa: BLE001 — if history is corrupt treat it as empty; don't let the chat window fail to open
        return {"messages": []}
    if not isinstance(raw, list):
        return {"messages": []}

    out: list[dict] = []
    for m in raw:
        parts = (m or {}).get("parts") or []
        text_bits: list[str] = []
        cards: list[dict] = []
        role = ""
        for p in parts:
            kind = p.get("part_kind")
            if kind == "user-prompt":
                role = "user"
                # Prefix blocks (【花钱】【降级】【当前流程】【页面上下文】 ("spending", "degradation", "current flow", "page context")...) are for the model, not typed by the user.
                # Each line starts with 【 and the user's words are in the last paragraph — strip line by line and what remains is what they said.
                body = "\n".join(ln for ln in str(p.get("content") or "").splitlines()
                                  if not ln.lstrip().startswith("【"))
                # The page-context lines don't start with 【 (available actions / navigate targets...), so add another fallback pass
                body = body.split("如需替用户操作页面,调用 control_page")[-1]
                body = body.split("可用动作:")[-1]
                body = body.lstrip(",,。").strip()
                if body:
                    text_bits.append(body)
            elif kind == "text":
                role = role or "assistant"
                if p.get("content"):
                    text_bits.append(str(p["content"]))
            elif kind == "tool-return" and p.get("tool_name") in CARD_TOOLS:
                role = role or "assistant"
                cards.append({"tool": p["tool_name"], "data": p.get("content")})
        if not text_bits and not cards:
            continue
        out.append({"role": role or "assistant", "text": "\n".join(text_bits), "cards": cards})

    lim = max(1, min(int(limit or 20), 60))
    return {"messages": out[-lim:], "scope": which}


@router.post("/diagnose")
async def diagnose(
    topic_id: int | None = None,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Run a diagnosis on the current topic and return evidence-backed root causes (non-streaming). Heavy work -> use the pro model."""
    from agents.geo.runtime.limits import run_guarded, usage_limits_for

    agent = _load_agent("pro")
    deps = resolve_account(current_user, db, topic_id=topic_id)
    t0 = time.perf_counter()
    try:
        result = await run_guarded(
            agent.run("请对当前主题跑 GEO 诊断,并给出带证据的根因结论。", deps=deps,
                      usage_limits=usage_limits_for("pro")),
            profile="pro")
    except Exception as e:  # noqa: BLE001
        _record_agent_usage(None, scene="pro", scope="diagnose", deps=deps, t0=t0,
                            status="http_error", error=e)
        raise
    _record_agent_usage(result, scene="pro", scope="diagnose", deps=deps, t0=t0)
    return {"output": result.output}


@router.get("/notifications")
async def list_notifications(
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Fetch the current account's unread proactive notifications (high-risk sentiment, etc.), shown when the chat window opens."""
    from geo.models.agent import AgentNotificationORM

    rows = (
        db.query(AgentNotificationORM)
        .filter(AgentNotificationORM.account_id == current_user.id, AgentNotificationORM.read_at.is_(None))
        .order_by(AgentNotificationORM.id.desc())
        .limit(20)
        .all()
    )
    return {"notifications": [{"id": r.id, "title": r.title, "body": r.body,
                               "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]}


@router.post("/notifications/read")
async def mark_notifications_read(
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark all unread notifications of the current account as read (called after the chat window shows them)."""
    from datetime import datetime

    from geo.models.agent import AgentNotificationORM

    (db.query(AgentNotificationORM)
     .filter(AgentNotificationORM.account_id == current_user.id, AgentNotificationORM.read_at.is_(None))
     .update({AgentNotificationORM.read_at: datetime.utcnow()}, synchronize_session=False))
    db.commit()
    return {"ok": True}
