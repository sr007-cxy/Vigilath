"""飞书 IM 接入器(自建应用模式)。

客户在自己飞书建自建应用 → 把 App ID/Secret/Verify Token 粘进控制台连接器 →
事件订阅回调填 `<host>/api/agent/im/feishu/callback`。

流程:飞书事件 → 按 app_id 反查 connector → account_id → 跑 agent → 调飞书 API 回贴。
回调必须 3s 内 200,故 agent(慢)放后台任务,回答用消息 API 异步发回。
"""
from __future__ import annotations

import json
import re
import time

import httpx


def _tables_to_lines(text: str) -> str:
    """飞书卡片 markdown 不渲染表格(`| a | b |` 会裸露),把 Markdown 表格转成可读列表行。

    2 列 → `• 左:右`;多列 → `• 表头1 值1 · 表头2 值2 …`。非表格内容原样保留。
    """
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    sep = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")   # 分隔行 |---|---|
    while i < len(lines):
        cur = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if "|" in cur and "|" in nxt and "-" in nxt and sep.match(nxt):
            header = [c.strip() for c in cur.strip().strip("|").split("|")]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if len(cells) == 2:
                    out.append(f"• {cells[0]}:{cells[1]}")
                else:
                    parts = [f"{header[j] + ' ' if j < len(header) and header[j] else ''}{c}".strip()
                             for j, c in enumerate(cells)]
                    out.append("• " + " · ".join(p for p in parts if p))
                i += 1
            continue
        out.append(cur)
        i += 1
    return "\n".join(out)


def _to_feishu_md(text: str) -> str:
    """转成飞书卡片能渲染的 Markdown:# 标题→加粗、表格→列表行;加粗/列表/代码飞书本就认。"""
    t = re.sub(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+(.*?)[ \t]*#*$", r"**\1**", text or "")
    return _tables_to_lines(t)


_SEP = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")


def _segment(text: str) -> list:
    """把文本切成块:('md', 文本) 或 ('table', 表头, 行列表)。"""
    lines = (text or "").split("\n")
    blocks: list = []
    buf: list[str] = []
    i = 0

    def flush():
        if buf:
            blocks.append(("md", "\n".join(buf)))
            buf.clear()

    while i < len(lines):
        cur = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if "|" in cur and "|" in nxt and "-" in nxt and _SEP.match(nxt):
            flush()
            header = [c.strip() for c in cur.strip().strip("|").split("|")]
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            blocks.append(("table", header, rows))
            continue
        buf.append(cur)
        i += 1
    flush()
    return blocks


def _build_card_v2(text: str) -> dict:
    """构造飞书 2.0 卡片:Markdown 段落 + **真表格组件**(table)。供有表格时渲染成真正的表格。"""
    header_to_bold = lambda s: re.sub(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+(.*?)[ \t]*#*$", r"**\1**", s)  # noqa: E731
    elements: list = []
    for blk in _segment(text):
        if blk[0] == "md":
            content = header_to_bold(blk[1]).strip()
            if content:
                elements.append({"tag": "markdown", "content": content})
        else:
            _, header, rows = blk
            ncols = max([len(header)] + [len(r) for r in rows]) if rows else len(header)
            cols = [{"name": f"c{j}",
                     "display_name": (header[j] if j < len(header) and header[j] else f"列{j + 1}"),
                     "data_type": "text"} for j in range(ncols)]
            trows = [{f"c{j}": (r[j] if j < len(r) else "") for j in range(ncols)} for r in rows]
            elements.append({"tag": "table", "page_size": min(max(len(trows), 1), 10),
                             "row_height": "low", "columns": cols, "rows": trows})
    if not elements:
        elements = [{"tag": "markdown", "content": text or "（无输出）"}]
    # 2.0 卡片外层只要 schema + body(wide_screen_mode 是 1.0 字段,2.0 放进来会被拒)
    return {"schema": "2.0", "body": {"elements": elements}}


def _has_table(text: str) -> bool:
    return any(b[0] == "table" for b in _segment(text))


class _Log:
    """直接 print 到 stdout(journald 必收);自定义 logger 不一定挂到 uvicorn handler。"""

    def info(self, msg, *a):
        print(msg % a if a else msg, flush=True)

    warning = info


log = _Log()
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


_bot_id_cache: dict[str, str] = {}


async def _bot_open_id(app_id: str, app_secret: str) -> str:
    """取机器人自己的 open_id(判断群里是否被 @ 用),缓存。"""
    if app_id in _bot_id_cache:
        return _bot_id_cache[app_id]
    tok = await _tenant_token(app_id, app_secret)
    if not tok:
        return ""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{FEISHU_BASE}/bot/v3/info", headers={"Authorization": f"Bearer {tok}"})
        oid = (r.json().get("bot") or {}).get("open_id", "")
        if oid:
            _bot_id_cache[app_id] = oid
        return oid
    except Exception:  # noqa: BLE001
        return ""


async def _post_card(tok: str, chat_id: str, card: dict) -> int | None:
    """发一张交互卡片,返回飞书 code(0=成功,None=异常)。"""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{FEISHU_BASE}/im/v1/messages",
                params={"receive_id_type": "chat_id"},
                headers={"Authorization": f"Bearer {tok}"},
                json={"receive_id": chat_id, "msg_type": "interactive", "content": json.dumps(card, ensure_ascii=False)},
            )
        return r.json().get("code")
    except Exception as e:  # noqa: BLE001
        log.warning("[im-feishu] 发消息异常:%s", e)
        return None


async def send_reply(tok: str, chat_id: str, text: str) -> None:
    """回贴飞书:有表格 → 渲染成真表格卡片(2.0);失败/无表格 → 退回 markdown(表格转列表行)卡片。"""
    if _has_table(text):
        code = await _post_card(tok, chat_id, _build_card_v2(text))
        if code == 0:
            log.info("[im-feishu] 已回贴(表格卡片)chat=%s", chat_id)
            return
        log.warning("[im-feishu] 表格卡片被拒(code=%s),回退列表行", code)
    simple = {"config": {"wide_screen_mode": True}, "elements": [{"tag": "markdown", "content": _to_feishu_md(text)}]}
    code = await _post_card(tok, chat_id, simple)
    if code not in (0, None):
        log.warning("[im-feishu] 发消息失败 code=%s(多半缺 im:message 发送权限)", code)
    elif code == 0:
        log.info("[im-feishu] 已回贴 chat=%s", chat_id)


async def _send_text(app_id: str, app_secret: str, chat_id: str, text: str) -> None:
    tok = await _tenant_token(app_id, app_secret)
    if not tok:
        log.warning("[im-feishu] 拿不到 tenant_access_token(app_id/secret 错?或缺权限),无法回贴")
        return
    await send_reply(tok, chat_id, text)


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
    log.info("[im-feishu] 收到回调:type=%s event_type=%s keys=%s",
             body.get("type"), (body.get("header") or {}).get("event_type"), list(body.keys()))

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
            if msg.get("message_type") == "text":
                chat_id = msg.get("chat_id") or ""
                chat_type = msg.get("chat_type") or ""
                mentions = msg.get("mentions") or []
                try:
                    text = json.loads(msg.get("content") or "{}").get("text", "").strip()
                except json.JSONDecodeError:
                    text = ""
                text = re.sub(r"@_user_\d+", "", text).strip()    # 去掉所有 @占位符
                # 群聊只在 @机器人 时才回;单聊照常回(避免群里逢消息必回)
                if chat_type == "group":
                    bot_oid = await _bot_open_id(app_id, conn.app_secret)
                    mentioned = (any((m.get("id") or {}).get("open_id") == bot_oid for m in mentions)
                                 if bot_oid else bool(mentions))
                    if not mentioned:
                        log.info("[im-feishu] 群消息未@机器人,忽略 chat=%s", chat_id)
                        return {"code": 0}
                if chat_id and text:
                    if conn.last_chat_id != chat_id:        # 记最近会话,供主动推送回推
                        conn.last_chat_id = chat_id
                        db.commit()
                    log.info("[im-feishu] 消息→后台跑 agent:account=%s chat=%s text=%r", conn.account_id, chat_id, text[:50])
                    bg.add_task(_handle_message, app_id, conn.app_secret, conn.account_id, chat_id, text)
                else:
                    log.info("[im-feishu] 文本消息但 chat_id/text 为空,跳过")
            else:
                log.info("[im-feishu] 非文本消息(type=%s),跳过", msg.get("message_type"))
        else:
            log.info("[im-feishu] 非消息事件(event_type=%s),跳过", header.get("event_type"))
        return {"code": 0}
    finally:
        db.close()
