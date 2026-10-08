"""DingTalk IM connector (enterprise internal bot, HTTP callback mode).

Simpler than WeCom: callbacks are JSON, signatures use HMAC-SHA256; replies POST directly to the sessionWebhook in the callback (no access_token needed).
DingTalk markdown supports # headings/bold but not tables -> tables are converted to list rows.

Connector (agent_im_connectors, platform='dingtalk'):
  app_id=bot AppKey/robotCode, app_secret=AppSecret (used for signature verification)
Callback URL (configured once as the DingTalk bot's "message receiving URL"): <host>/api/agent/im/dingtalk/callback
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time

import httpx
from fastapi import APIRouter, BackgroundTasks, Request
from sqlalchemy.orm import Session

from agents.geo.deps import AgentDeps
from agents.geo.embed.dedup import seen_before
from agents.geo.embed.im_feishu import _tables_to_lines, log
from geo.database import SessionLocal
from geo.models.agent import AgentIMConnectorORM

router = APIRouter(prefix="/agent/im")
_seen: dict[str, float] = {}


def _to_dingtalk_md(text: str) -> str:
    return _tables_to_lines(text or "")[:3000]


def _verify(app_secret: str, timestamp: str, sign: str) -> bool:
    """DingTalk outbound signature: base64(HMAC-SHA256(appSecret, f"{timestamp}\\n{appSecret}"))."""
    if not (app_secret and timestamp and sign):
        return False
    base = f"{timestamp}\n{app_secret}".encode("utf-8")
    calc = base64.b64encode(hmac.new(app_secret.encode("utf-8"), base, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(calc, sign)


async def _reply(session_webhook: str, text: str) -> None:
    if not session_webhook:
        log.warning("[dingtalk] no sessionWebhook; cannot reply")
        return
    payload = {"msgtype": "markdown", "markdown": {"title": "GEO 助手", "text": _to_dingtalk_md(text)}}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(session_webhook, json=payload)
        d = r.json()
        if d.get("errcode") not in (0, None):
            log.warning("[dingtalk] reply failed errcode=%s errmsg=%s", d.get("errcode"), d.get("errmsg"))
        else:
            log.info("[dingtalk] replied")
    except Exception as e:  # noqa: BLE001
        log.warning("[dingtalk] reply error: %s", e)


async def _handle(account_id: int, session_webhook: str, text: str) -> None:
    from agents.geo.agent import build_embed_agent, route_line
    from agents.geo.methods import load_message_history, save_message_history

    await _reply(session_webhook, "⏳ 正在查询,请稍候…")    # Immediate acknowledgement (one sessionWebhook send)
    db = SessionLocal()
    try:
        deps = AgentDeps(account_id=account_id, user_id=account_id, db=db, caps=["read", "write"], channel="im")
        from agents.geo.runtime.limits import run_guarded, usage_limits_for
        result = await run_guarded(
            build_embed_agent(True, line=route_line(text), channel="im").run(
                text, deps=deps, message_history=load_message_history(deps),
                usage_limits=usage_limits_for('im')),
            profile='im')
        try:
            save_message_history(deps, result.all_messages_json())
        except Exception:  # noqa: BLE001
            pass
        await _reply(session_webhook, (result.output or "（无输出）").strip())
    except Exception as e:  # noqa: BLE001
        await _reply(session_webhook, f"抱歉,处理出错了:{e}")
    finally:
        db.close()


@router.post("/dingtalk/callback")
async def dingtalk_callback(request: Request, bg: BackgroundTasks):
    timestamp = request.headers.get("timestamp", "")
    sign = request.headers.get("sign", "")
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return {"errcode": 0}

    robot_code = body.get("robotCode") or body.get("chatbotUserId") or ""
    db = SessionLocal()
    try:
        # Find the connector by robotCode first; if not found, iterate and match by verifying the signature with appSecret
        conn = None
        if robot_code:
            conn = (db.query(AgentIMConnectorORM)
                    .filter(AgentIMConnectorORM.platform == "dingtalk", AgentIMConnectorORM.app_id == robot_code,
                            AgentIMConnectorORM.enabled == 1).first())
        candidates = [conn] if conn else db.query(AgentIMConnectorORM).filter(
            AgentIMConnectorORM.platform == "dingtalk", AgentIMConnectorORM.enabled == 1).all()
        conn = next((c for c in candidates if c and _verify(c.app_secret, timestamp, sign)), None)
        if conn is None:
            log.warning("[dingtalk] signature verification matched nothing (robotCode=%s)", robot_code)
            return {"errcode": 0}

        if (body.get("msgtype") or "") != "text":
            return {"errcode": 0}
        content = ((body.get("text") or {}).get("content") or "").strip()
        session_webhook = body.get("sessionWebhook") or ""
        msg_id = body.get("msgId") or ""
        if msg_id and await seen_before(msg_id):
            return {"errcode": 0}
        if content and session_webhook:
            log.info("[dingtalk] message account=%s text=%r", conn.account_id, content[:40])
            bg.add_task(_handle, conn.account_id, session_webhook, content)
        return {"errcode": 0}
    finally:
        db.close()
