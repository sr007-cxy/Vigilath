"""飞书 IM 接入器(自建应用模式)。

客户在自己飞书建自建应用 → 把 App ID/Secret/Verify Token 粘进控制台连接器 →
事件订阅回调填 `<host>/api/agent/im/feishu/callback`。

流程:飞书事件 → 按 app_id 反查 connector → account_id → 跑 agent → 调飞书 API 回贴。
回调必须 3s 内 200,故 agent(慢)放后台任务,回答用消息 API 异步发回。
"""
from __future__ import annotations

import asyncio
import json
import time

import httpx
from fastapi import APIRouter, BackgroundTasks, Request
from sqlalchemy.orm import Session

from geo.agent.deps import AgentDeps
from geo.database import SessionLocal
from geo.models.agent import AgentIMConnectorORM

router = APIRouter(prefix="/agent/im")

FEISHU_BASE = "https://open.feishu.cn/open-apis"
_token_cache: dict[str, tuple[str, float]] = {}     # app_id -> (tenant_access_token, expire_ts)
_seen_events: dict[str, float] = {}                  # event_id -> ts(去重,飞书会重投)


def _connector_by_app(db: Session, app_id: str) -> AgentIMConnectorORM | None:
    return (
        db.query(AgentIMConnectorORM)
        .filter(AgentIMConnectorORM.platform == "feishu", AgentIMConnectorORM.app_id == app_id, AgentIMConnectorORM.enabled == 1)
        .first()
    )


def _maybe_decrypt(body: dict, aes_key: str) -> dict:
    """飞书设了 Encrypt Key 时事件体是 {'encrypt': '...'};未设则明文直接返回。"""
    if "encrypt" not in body:
        return body
    import base64
    import hashlib

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # 延迟导入

    raw = base64.b64decode(body["encrypt"])
    key = hashlib.sha256(aes_key.encode()).digest()
    iv, ct = raw[:16], raw[16:]
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    plain = dec.update(ct) + dec.finalize()
    plain = plain[: -plain[-1]]                      # 去 PKCS7 padding
    return json.loads(plain.decode("utf-8"))


async def _tenant_token(app_id: str, app_secret: str) -> str | None:
    cached = _token_cache.get(app_id)
    if cached and cached[1] - 60 > time.monotonic():
        return cached[0]
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"{FEISHU_BASE}/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
            )
            d = r.json()
        tok = d.get("tenant_access_token")
        if tok:
            _token_cache[app_id] = (tok, time.monotonic() + int(d.get("expire", 7000)))
        return tok
    except Exception:  # noqa: BLE001
        return None


async def _send_text(app_id: str, app_secret: str, chat_id: str, text: str) -> None:
    tok = await _tenant_token(app_id, app_secret)
    if not tok:
        return
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            await c.post(
                f"{FEISHU_BASE}/im/v1/messages",
                params={"receive_id_type": "chat_id"},
                headers={"Authorization": f"Bearer {tok}"},
                json={"receive_id": chat_id, "msg_type": "text", "content": json.dumps({"text": text})},
            )
    except Exception:  # noqa: BLE001
        pass


async def _handle_message(app_id: str, app_secret: str, account_id: int, chat_id: str, user_text: str) -> None:
    """后台:跑 agent → 回贴飞书。复用账号级 agent + 多轮记忆(发布工具不暴露)。"""
    from geo.agent.agent import build_embed_agent
    from geo.agent.methods import load_message_history, save_message_history

    db = SessionLocal()
    try:
        deps = AgentDeps(account_id=account_id, user_id=account_id, db=db, caps=["read", "write"])
        agent = build_embed_agent(True)
        history = load_message_history(deps)
        result = await agent.run(user_text, deps=deps, message_history=history)
        try:
            save_message_history(deps, result.all_messages_json())
        except Exception:  # noqa: BLE001
            pass
        await _send_text(app_id, app_secret, chat_id, (result.output or "（无输出）").strip())
    except Exception as e:  # noqa: BLE001
        await _send_text(app_id, app_secret, chat_id, f"抱歉,处理出错了:{e}")
    finally:
        db.close()


@router.post("/feishu/callback")
async def feishu_callback(request: Request, bg: BackgroundTasks):
    body = await request.json()

    # 先按 app_id 找 connector(拿 aes_key 解密 / 拿 secret 回贴)。明文事件 app_id 在 header。
    app_id = (body.get("header") or {}).get("app_id") or body.get("app_id") or ""
    db = SessionLocal()
    try:
        conn = _connector_by_app(db, app_id) if app_id else None
        if conn and conn.aes_key:
            body = _maybe_decrypt(body, conn.aes_key)
            app_id = (body.get("header") or {}).get("app_id") or app_id
            if not conn:
                conn = _connector_by_app(db, app_id)

        # URL 校验(配置事件订阅时飞书发一次)
        if body.get("type") == "url_verification":
            return {"challenge": body.get("challenge", "")}

        header = body.get("header") or {}
        # 校验 verify token(连接器里存的)
        if conn and header.get("token") and conn.verify_token and header["token"] != conn.verify_token:
            return {"code": 0}        # token 不符,静默丢弃
        if conn is None:
            return {"code": 0}        # 未知 app,忽略

        # 去重 + 只处理用户文本消息
        eid = header.get("event_id") or ""
        now = time.time()
        if eid:
            for k in [k for k, t in _seen_events.items() if now - t > 600]:
                _seen_events.pop(k, None)
            if eid in _seen_events:
                return {"code": 0}
            _seen_events[eid] = now

        if header.get("event_type") == "im.message.receive_v1":
            event = body.get("event") or {}
            msg = event.get("message") or {}
            # 忽略机器人自己/非文本
            if msg.get("message_type") == "text":
                chat_id = msg.get("chat_id") or ""
                try:
                    text = json.loads(msg.get("content") or "{}").get("text", "").strip()
                except json.JSONDecodeError:
                    text = ""
                # 群里 @机器人会带 @_user_1 之类,去掉前导 @ 文本
                text = text.replace("@_user_1", "").strip()
                if chat_id and text:
                    account_id = conn.account_id
                    app_secret = conn.app_secret
                    bg.add_task(_handle_message, app_id, app_secret, account_id, chat_id, text)
        return {"code": 0}
    finally:
        db.close()
