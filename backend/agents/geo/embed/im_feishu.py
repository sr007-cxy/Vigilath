"""Feishu IM connector (self-built app mode).

The customer creates a self-built app in their own Feishu -> pastes App ID/Secret/Verify Token into the console connector ->
sets the event subscription callback to `<host>/api/agent/im/feishu/callback`.

Flow: Feishu event -> look up connector by app_id -> account_id -> run agent -> reply via the Feishu API.
The callback must return 200 within 3s, so the (slow) agent runs as a background task and the answer is sent back asynchronously via the message API.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time

import httpx


def _tables_to_lines(text: str) -> str:
    """Feishu card markdown doesn't render tables (`| a | b |` shows raw), so convert Markdown tables into readable list rows.

    2 columns -> `• left: right`; more columns -> `• header1 value1 · header2 value2 ...`. Non-table content is kept as-is.
    """
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    sep = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")   # Separator row |---|---|
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


def _clean_md(text: str) -> str:
    """Strip markdown markers Feishu renders unreliably -> plain text (Feishu table cells/some cards don't render ** and show it raw).

    # Headings -> drop the hashes; ---/***/___ separators -> delete; **bold**/*italic* -> drop the asterisks (the web react-markdown doesn't use this function and bolds as usual).
    """
    t = text or ""
    t = re.sub(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+(.*?)[ \t]*#*$", r"\1", t)        # Headings: drop # keep the text
    t = re.sub(r"(?m)^[ \t]{0,3}([*\-_])(?:[ \t]*\1){2,}[ \t]*$", "", t)      # Separator lines -> delete the whole line
    t = re.sub(r"\*\*\*([^*\n]+)\*\*\*", r"\1", t)                            # ***x***→x
    t = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", t)                                # **x**→x
    t = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", t)                       # *x*→x
    t = re.sub(r"\*{2,}", "", t)                                              # Leftover runs of asterisks -> delete
    return t


# Automatically convert bare URLs into explicit markdown links.
# Why: Feishu lark_md auto-links **bare** URLs, but the href gets truncated at characters like `,` `?` `&` `#` —
# Eastmoney Guba `.../news,ACME,123.html` (comma) and SERP links with `?query` open only half the URL.
# Wrapped as `[url](url)`, the markdown link ends at `)`, so commas/query params are preserved and the href is complete.
# A negative lookbehind skips URLs already inside `[..](..)` (preceded by `(` or `[`), avoiding double wrapping.
_BARE_URL_RE = re.compile(r"(?<![(\[])\bhttps?://[^\s<>()\[\]]+")


def _autolink_urls(text: str) -> str:
    return _BARE_URL_RE.sub(lambda m: f"[{m.group(0)}]({m.group(0)})", text or "")


def _to_feishu_md(text: str) -> str:
    """Convert to Markdown that Feishu cards can render: headings -> bold, clean separators, tables -> list rows, bare links -> explicit links; bold/lists/code are supported natively."""
    return _autolink_urls(_tables_to_lines(_clean_md(text)))


_SEP = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")


def _segment(text: str) -> list:
    """Split text into blocks: ('md', text) or ('table', headers, rows)."""
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
    """Build a Feishu 2.0 card: Markdown paragraphs + **real table components** (table). Used to render real tables when present."""
    elements: list = []
    for blk in _segment(text):
        if blk[0] == "md":
            content = _autolink_urls(_clean_md(blk[1])).strip()
            if content:
                elements.append({"tag": "markdown", "content": content})
        else:
            _, header, rows = blk
            ncols = max([len(header)] + [len(r) for r in rows]) if rows else len(header)
            cols = [{"name": f"c{j}",
                     "display_name": (_clean_md(header[j]) if j < len(header) and header[j] else f"列{j + 1}"),
                     "data_type": "text"} for j in range(ncols)]
            # Strip markdown markers from cells too (the table component shows plain text, ** would show raw)
            trows = [{f"c{j}": (_clean_md(r[j]) if j < len(r) else "") for j in range(ncols)} for r in rows]
            elements.append({"tag": "table", "page_size": min(max(len(trows), 1), 10),
                             "row_height": "low", "columns": cols, "rows": trows})
    if not elements:
        elements = [{"tag": "markdown", "content": text or "（无输出）"}]
    # The 2.0 card outer layer only needs schema + body (wide_screen_mode is a 1.0 field; including it in 2.0 gets rejected)
    return {"schema": "2.0", "body": {"elements": elements}}


def _has_table(text: str) -> bool:
    return any(b[0] == "table" for b in _segment(text))


class _Log:
    """Print directly to stdout (always captured by journald); a custom logger isn't necessarily attached to the uvicorn handler."""

    def info(self, msg, *a):
        print(msg % a if a else msg, flush=True)

    warning = info


log = _Log()
from fastapi import APIRouter, BackgroundTasks, Request
from sqlalchemy.orm import Session

from agents.geo.deps import AgentDeps
from agents.geo.embed.dedup import seen_before
from geo.database import SessionLocal
from geo.models.agent import AgentIMConnectorORM

router = APIRouter(prefix="/agent/im")

FEISHU_BASE = "https://open.feishu.cn/open-apis"
_token_cache: dict[str, tuple[str, float]] = {}     # app_id -> (tenant_access_token, expire_ts)


def _connector_by_app(db: Session, app_id: str) -> AgentIMConnectorORM | None:
    return (
        db.query(AgentIMConnectorORM)
        .filter(AgentIMConnectorORM.platform == "feishu", AgentIMConnectorORM.app_id == app_id, AgentIMConnectorORM.enabled == 1)
        .first()
    )


def _connector_by_token(db: Session, token: str) -> AgentIMConnectorORM | None:
    """Card button callbacks don't carry app_id; look up the connector by verify_token."""
    if not token:
        return None
    return (db.query(AgentIMConnectorORM)
            .filter(AgentIMConnectorORM.platform == "feishu", AgentIMConnectorORM.verify_token == token,
                    AgentIMConnectorORM.enabled == 1).first())


def _help_card() -> dict:
    """Full feature list card (1.0 lark_md, renders bold)."""
    return _text_card(_HELP_TEXT)


# Quick button defaults / parsing: see geo.im_quick_config (configurable per connector in the console; only default fallback here).
from geo.im_quick_config import DEFAULT_QUICK_ACTIONS as _DEF_QA  # noqa: E402
QUICK_ACTIONS = [(l, q) for l, q in _DEF_QA]


def _footer_buttons_1x(actions) -> dict:
    """Button row at the bottom of a 1.0 card (action + button, value.q callback)."""
    return {"tag": "action", "actions": [
        {"tag": "button", "text": {"tag": "plain_text", "content": label},
         "type": "default", "value": {"q": q}} for label, q in actions]}


def _with_footer(card: dict, actions=None) -> dict:
    """Append quick buttons to the bottom of the reply card (actions=[(label,q)...]; None uses defaults). Supports both 1.0/2.0; failure doesn't affect the body."""
    actions = actions or QUICK_ACTIONS
    try:
        if card.get("schema") == "2.0":
            cols = [{"tag": "column", "width": "auto", "vertical_align": "center", "elements": [
                {"tag": "button", "text": {"tag": "plain_text", "content": label}, "type": "default",
                 "behaviors": [{"type": "callback", "value": {"q": q}}]}]} for label, q in actions]
            card["body"]["elements"].append(
                {"tag": "column_set", "flex_mode": "flow", "horizontal_spacing": "8px", "columns": cols})
        else:
            card.setdefault("elements", []).append({"tag": "hr"})
            card["elements"].append(_footer_buttons_1x(actions))
    except Exception:  # noqa: BLE001 — if adding buttons fails send the original card without buttons; don't lose the body
        pass
    return card


def _menu_card() -> dict:
    """Quick action button card (1.0 action; clickable in group chats)."""
    btns = [{"tag": "button", "text": {"tag": "plain_text", "content": label},
             "type": "primary" if i == 0 else "default", "value": {"q": q}}
            for i, (label, q) in enumerate(QUICK_ACTIONS)]
    return {"config": {"wide_screen_mode": True}, "elements": [
        {"tag": "div", "text": {"tag": "lark_md", "content": "**Vigilath GEO 助手** —— 点按钮查询,或直接打字问我:"}},
        {"tag": "action", "actions": btns},
    ]}


_HELP_TEXT = """**Vigilath GEO 助手 · 全部功能**(直接打字问我即可)

**📊 数据查询**
- 最近数据怎么样 / 昨天的日报 / 本周周报 / 上个月月报
- 整体露出率、TOP1露出率、内容中正率、文章引用率、品牌声量度分别是多少
- 最近几天走势如何 / 有没有达标
- 累计被搜到几个问题 / 哪些问题还没被搜到
- 种子词几个、问题多少、每个种子扩展了几个
- 文章发布进度 / 今天发了哪些文章
- 今天舆情怎么样、有没有负面/风险
- 查我上传的资料(知识库)

**📝 诊断与内容**
- 跑 GEO 诊断、看诊断报告(带根因)
- 生成文章、审核文章(通过/驳回)

**⚙️ 配置**
- 设/扩展提示词、配置舆情监测关键词

**🔗 页面跳转**(网页端)
- 「去报告总览 / 日报 / 周报 / 证据中心」带你跳到对应页

> 真实对外发布不在对话内开放(平台护栏)。也可用输入框底部的菜单按钮。"""


def _maybe_decrypt(body: dict, aes_key: str) -> dict:
    """When Feishu has an Encrypt Key set the event body is {'encrypt': '...'}; otherwise plaintext is returned directly."""
    if "encrypt" not in body:
        return body
    import base64
    import hashlib

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # Lazy import

    raw = base64.b64decode(body["encrypt"])
    key = hashlib.sha256(aes_key.encode()).digest()
    iv, ct = raw[:16], raw[16:]
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    plain = dec.update(ct) + dec.finalize()
    plain = plain[: -plain[-1]]                      # Remove PKCS7 padding
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
    """Get the bot's own open_id (used to tell whether it was @-mentioned in a group), cached."""
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


async def list_bot_chats(tok: str) -> list[str]:
    """List chat_ids of **all groups** the bot is in (paginated). Used for proactive broadcast pushes (briefs/alerts) instead of only last_chat_id. Returns empty on failure."""
    out: list[str] = []
    page_token = ""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            for _ in range(10):                      # At most 10 pages ×100 = 1000 groups, enough
                params = {"page_size": 100}
                if page_token:
                    params["page_token"] = page_token
                r = await c.get(f"{FEISHU_BASE}/im/v1/chats", params=params,
                                headers={"Authorization": f"Bearer {tok}"})
                data = (r.json() or {}).get("data") or {}
                out.extend(it["chat_id"] for it in (data.get("items") or []) if it.get("chat_id"))
                page_token = data.get("page_token") or ""
                if not data.get("has_more") or not page_token:
                    break
    except Exception as e:  # noqa: BLE001
        log.warning("[im-feishu] failed to list groups: %s", e)
    return out


async def _post_card(tok: str, receive_id: str, card: dict, rid_type: str = "chat_id") -> int | None:
    """Send an interactive card and return the Feishu code (0 = success, None = exception). rid_type: chat_id / open_id."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{FEISHU_BASE}/im/v1/messages",
                params={"receive_id_type": rid_type},
                headers={"Authorization": f"Bearer {tok}"},
                json={"receive_id": receive_id, "msg_type": "interactive", "content": json.dumps(card, ensure_ascii=False)},
            )
        return r.json().get("code")
    except Exception as e:  # noqa: BLE001
        log.warning("[im-feishu] send message error: %s", e)
        return None


async def _send_card_id(tok: str, receive_id: str, card: dict, rid_type: str = "chat_id") -> str:
    """Send a card and return message_id (for updating the same card in place later)."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"{FEISHU_BASE}/im/v1/messages", params={"receive_id_type": rid_type},
                headers={"Authorization": f"Bearer {tok}"},
                json={"receive_id": receive_id, "msg_type": "interactive", "content": json.dumps(card, ensure_ascii=False)},
            )
        return ((r.json().get("data") or {}).get("message_id")) or ""
    except Exception as e:  # noqa: BLE001
        log.warning("[im-feishu] send card error: %s", e)
        return ""


async def _patch_card(tok: str, message_id: str, card: dict) -> bool:
    """Update a sent card in place (turn the 「正在查询」 ("Querying") card into the result). Returns True on success."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.patch(
                f"{FEISHU_BASE}/im/v1/messages/{message_id}",
                headers={"Authorization": f"Bearer {tok}"},
                json={"content": json.dumps(card, ensure_ascii=False)},
            )
        return r.json().get("code") == 0
    except Exception as e:  # noqa: BLE001
        log.warning("[im-feishu] update card error: %s", e)
        return False


def _text_card(text: str) -> dict:
    """Plain text/bold card: uses 1.0 div+lark_md (Feishu's most reliable bold/link rendering; the `markdown` tag may show raw `**`)."""
    return {"config": {"wide_screen_mode": True},
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": _to_feishu_md(text)}}]}


def _result_card(text: str, actions=None) -> dict:
    """Result card: 2.0 table when there are tables; otherwise 1.0 div+lark_md (reliably renders **bold**). Quick buttons appended at the bottom (per connector config)."""
    card = _build_card_v2(text) if _has_table(text) else _text_card(text)
    return _with_footer(card, actions)


async def send_reply(tok: str, receive_id: str, text: str, rid_type: str = "chat_id", actions=None) -> None:
    """Reply on Feishu: tables -> 2.0 table card, otherwise -> 1.0 lark_md card; fall back to plain text on failure. rid_type: chat_id / open_id."""
    code = await _post_card(tok, receive_id, _result_card(text, actions), rid_type)
    if code == 0:
        log.info("[im-feishu] replied to=%s", receive_id)
        return
    log.warning("[im-feishu] card send failed code=%s; falling back to plain text", code)
    try:                                  # Fallback: plain text (markdown markers stripped, at least no raw symbols)
        plain = re.sub(r"[*#`]", "", _to_feishu_md(text))
        async with httpx.AsyncClient(timeout=15) as c:
            await c.post(f"{FEISHU_BASE}/im/v1/messages", params={"receive_id_type": rid_type},
                         headers={"Authorization": f"Bearer {tok}"},
                         json={"receive_id": receive_id, "msg_type": "text", "content": json.dumps({"text": plain})})
    except Exception as e:  # noqa: BLE001
        log.warning("[im-feishu] plain-text fallback also failed: %s", e)


async def send_webhook(url: str, text: str, secret: str = "") -> bool:
    """Push to a Feishu **custom bot webhook** (a "Custom Bot" added to a group; just paste the URL, optional signature).

    Uses the same card as send_reply; when secret is non-empty, signs with HMAC-SHA256 per Feishu's algorithm
    (string_to_sign = f"{timestamp}\\n{secret}", empty body). Best-effort; returns whether it succeeded.
    """
    payload: dict = {"msg_type": "interactive", "card": _result_card(text)}
    if secret:
        ts = str(int(time.time()))
        digest = hmac.new(f"{ts}\n{secret}".encode("utf-8"), b"", hashlib.sha256).digest()
        payload["timestamp"] = ts
        payload["sign"] = base64.b64encode(digest).decode("utf-8")
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(url, json=payload)
        d = r.json() or {}
        ok = d.get("code", d.get("StatusCode", 0)) == 0
        if not ok:
            log.warning("[im-feishu] webhook returned non-zero: %s", str(d)[:120])
        return ok
    except Exception as e:  # noqa: BLE001
        log.warning("[im-feishu] webhook push error: %s", e)
        return False


async def _send_text(app_id: str, app_secret: str, receive_id: str, text: str, rid_type: str = "chat_id",
                     actions=None) -> None:
    tok = await _tenant_token(app_id, app_secret)
    if not tok:
        log.warning("[im-feishu] cannot get tenant_access_token (wrong app_id/secret? or missing permissions); unable to reply")
        return
    await send_reply(tok, receive_id, text, rid_type, actions)


def _conn_actions(app_id: str):
    """Get the quick buttons configured for this connector by app_id (defaults if unconfigured). Separate session, read-only."""
    from geo.im_quick_config import quick_actions
    db = SessionLocal()
    try:
        conn = _connector_by_app(db, app_id)
        return quick_actions(conn.config_json if conn else None)
    finally:
        db.close()


async def _handle_message(app_id: str, app_secret: str, account_id: int, receive_id: str, user_text: str,
                          rid_type: str = "chat_id") -> None:
    """Background: run the agent -> reply on Feishu. Reuses the account-level agent + multi-turn memory (publish tools not exposed). rid_type: chat_id / open_id."""
    from agents.geo.agent import build_embed_agent, route_line
    from agents.geo.methods import load_message_history, save_message_history

    actions = _conn_actions(app_id)        # Bottom quick buttons configured for this connector (editable in the console)
    # Immediate ack + in-place update: first send a 「正在查询」 ("Querying") card to get message_id, then PATCH the same card into the result
    # (Feishu doesn't support web-style token-by-token SSE, but sent cards can be updated, so it feels like "placeholder -> replace" rather than two messages).
    tok = await _tenant_token(app_id, app_secret)
    msg_id = ""
    if tok:
        msg_id = await _send_card_id(tok, receive_id, _text_card("⏳ 正在查询,请稍候…"), rid_type)
    db = SessionLocal()
    try:
        deps = AgentDeps(account_id=account_id, user_id=account_id, db=db, caps=["read", "write"], channel="im")
        agent = build_embed_agent(True, line=route_line(user_text), channel="im")   # Route by message to the GEO / sentiment monitoring toolset
        history = load_message_history(deps)
        from agents.geo.runtime.limits import run_guarded, usage_limits_for
        result = await run_guarded(
            agent.run(user_text, deps=deps, message_history=history,
                      usage_limits=usage_limits_for('im')),
            profile='im')
        try:
            save_message_history(deps, result.all_messages_json())
        except Exception:  # noqa: BLE001
            pass
        text = (result.output or "（无输出）").strip()
    except Exception as e:  # noqa: BLE001
        from agents.geo.runtime.limits import public_message
        text = public_message(e) or f"抱歉,处理出错了:{e}"
    finally:
        db.close()
    # Update the placeholder card to the result; if the update fails (e.g. past the time window) fall back to sending a new message
    if not (tok and msg_id and await _patch_card(tok, msg_id, _result_card(text, actions))):
        await _send_text(app_id, app_secret, receive_id, text, rid_type, actions)


@router.post("/feishu/callback")
async def feishu_callback(request: Request, bg: BackgroundTasks):
    body = await request.json()
    log.info("[im-feishu] callback received: type=%s event_type=%s keys=%s",
             body.get("type"), (body.get("header") or {}).get("event_type"), list(body.keys()))

    # First find the connector by app_id (to get aes_key for decryption / secret for replies). For plaintext events app_id is in the header.
    app_id = (body.get("header") or {}).get("app_id") or body.get("app_id") or ""
    db = SessionLocal()
    try:
        conn = _connector_by_app(db, app_id) if app_id else None
        if conn and conn.aes_key:
            body = _maybe_decrypt(body, conn.aes_key)
            app_id = (body.get("header") or {}).get("app_id") or app_id
            if not conn:
                conn = _connector_by_app(db, app_id)

        # URL verification (sent once by Feishu when configuring the event subscription)
        if body.get("type") == "url_verification":
            return {"challenge": body.get("challenge", "")}

        # Card button click callback (group quick buttons): legacy top-level action / v2 event.action
        action = body.get("action") or (body.get("event") or {}).get("action")
        if action and isinstance(action, dict) and action.get("value"):
            ev = body.get("event") or {}
            log.info("[im-feishu] card button callback keys=%s value=%s", list(body.keys()), action.get("value"))
            q = (action.get("value") or {}).get("q")
            tok_field = body.get("token") or (body.get("header") or {}).get("token") or ""
            c = _connector_by_token(db, tok_field) or (_connector_by_app(db, app_id) if app_id else None)
            chat = (body.get("open_chat_id") or ev.get("open_chat_id")
                    or ((ev.get("context") or {}).get("open_chat_id")) or "")
            oid = (body.get("open_id") or ((ev.get("operator") or {}).get("operator_id") or {}).get("open_id") or "")
            # Dedup: Feishu delivers one click twice, in legacy + 2.0 formats (different field locations, no shared event_id),
            # so use "connector account + query" as the key (both carry value.q and resolve to the same connector) + 10s TTL,
            # collapsing the near-simultaneous double delivery; clicking the same button again after 10 seconds still responds.
            acct = c.account_id if c else (app_id or "")
            ckey = f"cardcb:{acct}:{q}"
            if q and await seen_before(ckey, ttl=10):
                log.info("[im-feishu] duplicate card callback delivery, ignored key=%s", ckey)
                return {}
            if c and q and chat:
                bg.add_task(_handle_message, c.app_id, c.app_secret, c.account_id, chat, q, "chat_id")
            elif c and q and oid:
                bg.add_task(_handle_message, c.app_id, c.app_secret, c.account_id, oid, q, "open_id")
            return {}        # Card callback: a 200 with an empty body is enough

        header = body.get("header") or {}
        # Verify the verify token (stored in the connector)
        if conn and header.get("token") and conn.verify_token and header["token"] != conn.verify_token:
            return {"code": 0}        # Token mismatch, silently drop
        if conn is None:
            return {"code": 0}        # Unknown app, ignore

        # Dedup (cross-worker, shared via Redis) + only handle user text messages
        eid = header.get("event_id") or ""
        if eid and await seen_before(eid):
            return {"code": 0}

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
                text = re.sub(r"@_user_\d+", "", text).strip()    # Remove all @ placeholders
                # In group chats reply only when the bot is @-mentioned; DMs reply as usual (avoid replying to every group message)
                if chat_type == "group":
                    bot_oid = await _bot_open_id(app_id, conn.app_secret)
                    mentioned = (any((m.get("id") or {}).get("open_id") == bot_oid for m in mentions)
                                 if bot_oid else bool(mentions))
                    if not mentioned:
                        log.info("[im-feishu] group message did not @ the bot, ignored chat=%s", chat_id)
                        return {"code": 0}
                if chat_id and text:
                    if conn.last_chat_id != chat_id:        # Record the latest chat for proactive push-back
                        conn.last_chat_id = chat_id
                        db.commit()
                    # 「重新开始/清空记忆」 ("start over/clear memory") -> clear this account's conversation memory (removes stale context after renaming/deleting topics)
                    if text in ("重新开始", "清空记忆", "清空对话", "重置", "清除记忆") or text.lower() == "reset":
                        from agents.geo.methods import reset_conversation
                        try:
                            reset_conversation(AgentDeps(account_id=conn.account_id, user_id=conn.account_id, db=db))
                        except Exception:  # noqa: BLE001
                            pass
                        tok = await _tenant_token(app_id, conn.app_secret)
                        if tok:
                            await _post_card(tok, chat_id, _text_card("✅ 已清空对话记忆,后续以最新数据为准。"))
                        return {"code": 0}
                    # 「功能/菜单/帮助/能做什么」 ("features/menu/help/what can you do") -> always a plain-text capability list (no button card). Doesn't run the agent
                    low = text.lower()
                    if (text in ("功能", "全部功能", "所有功能", "能做什么", "你能做什么", "帮助", "菜单", "开始")
                            or low in ("help", "/help", "menu", "/menu")):
                        tok = await _tenant_token(app_id, conn.app_secret)
                        if tok:
                            await _post_card(tok, chat_id, _help_card())
                        return {"code": 0}
                    log.info("[im-feishu] message -> running agent in background: account=%s chat=%s text=%r", conn.account_id, chat_id, text[:50])
                    bg.add_task(_handle_message, app_id, conn.app_secret, conn.account_id, chat_id, text)
                else:
                    log.info("[im-feishu] text message but chat_id/text is empty, skipped")
            else:
                log.info("[im-feishu] non-text message (type=%s), skipped", msg.get("message_type"))
        elif header.get("event_type") == "im.chat.access_event.bot_p2p_chat_entered_v1":
            log.info("[im-feishu] user entered the chat (no auto card popup)")
        elif header.get("event_type") == "application.bot.menu_v6":
            # The "bot menu" configured in the Feishu console was clicked: event_key maps to a query (menu items configured in the console)
            ev = body.get("event") or {}
            key = ev.get("event_key") or ""
            oid = ((ev.get("operator") or {}).get("operator_id") or {}).get("open_id") or ""
            log.info("[im-feishu] bot menu click event_key=%r op=%s", key, oid)
            from geo.im_quick_config import menu_map
            q = menu_map(conn.config_json if conn else None).get(key)   # Menu mapping configured for this connector (editable in the console)
            if q is None and key and len(key) <= 40:   # Key not in the table: ask it directly as a query
                q = key
            if conn and oid:
                tok = await _tenant_token(app_id, conn.app_secret)
                if not tok:
                    return {"code": 0}
                if q in (None, "__help__"):
                    await _post_card(tok, oid, _help_card(), "open_id")
                else:
                    bg.add_task(_handle_message, app_id, conn.app_secret, conn.account_id, oid, q, "open_id")
        else:
            log.info("[im-feishu] non-message event (event_type=%s), skipped", header.get("event_type"))
        return {"code": 0}
    finally:
        db.close()
