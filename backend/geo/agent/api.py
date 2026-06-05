"""/api/agent/* 路由。模块级不导入 pydantic-ai(在端点内惰性导入),保证未安装时也能加载、不影响主应用。

注册(在 geo/main.py):
    from geo.agent import api as agent_api
    app.include_router(agent_api.router, prefix="/api", tags=["agent"])
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from geo.agent.auth import get_current_user          # 独立校验,不 import geo.api
from geo.agent.methods import (
    load_message_history,
    reset_conversation,
    resolve_account,
    save_message_history,
)
from geo.database import SessionLocal
from geo.models.user import UserORM

# 这些「数据工具」的返回会作为结构化卡片推给前端渲染
CARD_TOOLS = {"get_report", "get_growth_summary", "get_today_effect", "get_query_coverage", "get_publish_status", "get_batch_results"}

router = APIRouter(prefix="/agent")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class ChatIn(BaseModel):
    message: str
    topic_id: int | None = None


def _load_agent():
    try:
        from geo.agent.agent import build_agent
        return build_agent()
    except Exception as e:  # pydantic-ai 未装 / 缺 DEEPSEEK_API_KEY
        raise HTTPException(status_code=503, detail=f"Agent 暂不可用:{e}")


@router.post("/chat")
async def chat(
    body: ChatIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """对话(SSE 流式)。会话单位 = 账号;多轮记忆落库 + 数据工具结果作结构化卡片推前端。"""
    agent = _load_agent()
    deps = resolve_account(current_user, db, topic_id=body.topic_id)
    history = load_message_history(deps)   # 多轮:载入账号历史

    async def event_stream():
        try:
            # 必须用 agent.run() 跑完整「模型→工具→模型」循环。
            # run_stream()+stream_text() 在 DeepSeek 同时吐文本+tool_call 时会把文本当最终结果,
            # 工具被标成 "Tool not executed - a final result was already processed",卡片全空(0/0)。
            result = await agent.run(body.message, deps=deps, message_history=history)

            # 伪流式:把最终文本分块推前端,保留逐字 UX
            text = result.output or ""
            step = 24
            for i in range(0, len(text), step):
                yield f"data: {json.dumps({'delta': text[i:i + step]}, ensure_ascii=False)}\n\n"

            # 抽真实执行的数据工具返回 → 结构化卡片(去重:同工具留最后一次)
            cards_by_tool: dict[str, dict] = {}
            for m in result.all_messages():
                for p in getattr(m, "parts", []):
                    if getattr(p, "part_kind", "") == "tool-return" and getattr(p, "tool_name", "") in CARD_TOOLS:
                        cards_by_tool[p.tool_name] = {"tool": p.tool_name, "data": p.content}
            cards = list(cards_by_tool.values())
            if cards:
                yield f"data: {json.dumps({'cards': cards}, ensure_ascii=False, default=str)}\n\n"

            # 落库多轮记忆
            try:
                save_message_history(deps, result.all_messages_json())
            except Exception:  # noqa: BLE001 — 存历史失败不影响本轮回答
                pass
            yield "data: {\"done\": true}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/reset")
async def reset(
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """清空当前账号的对话记忆(用户「重新开始」)。"""
    deps = resolve_account(current_user, db)
    reset_conversation(deps)
    return {"ok": True}


@router.post("/diagnose")
async def diagnose(
    topic_id: int | None = None,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """对当前主题跑诊断并给带证据根因(非流式)。"""
    agent = _load_agent()
    deps = resolve_account(current_user, db, topic_id=topic_id)
    result = await agent.run("请对当前主题跑 GEO 诊断,并给出带证据的根因结论。", deps=deps)
    return {"output": result.output}
