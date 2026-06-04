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
from geo.agent.methods import resolve_account
from geo.database import SessionLocal
from geo.models.user import UserORM

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
    """对话(SSE 流式)。会话单位 = 账号;每次请求新建 deps 注入账号上下文。"""
    agent = _load_agent()
    deps = resolve_account(current_user, db, topic_id=body.topic_id)

    async def event_stream():
        try:
            async with agent.run_stream(body.message, deps=deps) as result:
                async for delta in result.stream_text(delta=True):
                    yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
            yield "data: {\"done\": true}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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
