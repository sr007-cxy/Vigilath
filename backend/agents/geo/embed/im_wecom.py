"""WeCom IM connector (self-built app mode).

Completely different from Feishu: callbacks use **XML + mandatory AES encryption** (WXBizMsgCrypt), and URL verification is a GET echostr.
Replies use app messages message/send (markdown; WeCom doesn't render tables -> converted to list rows; WeCom supports # headings, so they're kept).

Connector (agent_im_connectors, platform='wecom'):
  app_id=CorpID, app_secret=Secret, verify_token=Token, aes_key=EncodingAESKey, config_json={"agentid": N}
Callback URL (configured once in WeCom "Receive Messages"): <host>/api/agent/im/wecom/callback
"""
from __future__ import annotations

import base64
import hashlib
import json
import struct
import time
import xml.etree.ElementTree as ET

import httpx
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from agents.geo.deps import AgentDeps
from agents.geo.embed.dedup import seen_before
from agents.geo.embed.im_feishu import _tables_to_lines, log
from geo.database import SessionLocal
from geo.models.agent import AgentIMConnectorORM

router = APIRouter(prefix="/agent/im")
QYAPI = "https://qyapi.weixin.qq.com/cgi-bin"
_token_cache: dict[str, tuple[str, float]] = {}
_seen: dict[str, float] = {}


def _to_wecom_md(text: str) -> str:
    """WeCom markdown: supports # headings/bold but not tables -> tables converted to list rows; truncated to ~2000 characters."""
    return _tables_to_lines(text or "")[:2000]


def _sign(token: str, timestamp: str, nonce: str, encrypt: str) -> str:
    return hashlib.sha1("".join(sorted([token, timestamp, nonce, encrypt])).encode()).hexdigest()


def _decrypt(encrypt_b64: str, encoding_aes_key: str) -> tuple[str, str]:
    """Returns (plaintext message, receiveid)."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = base64.b64decode(encoding_aes_key + "=")
    data = base64.b64decode(encrypt_b64)
    dec = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
    plain = dec.update(data) + dec.finalize()
    plain = plain[: -plain[-1]]                              # PKCS7
    msg_len = struct.unpack(">I", plain[16:20])[0]
    return plain[20:20 + msg_len].decode("utf-8"), plain[20 + msg_len:].decode("utf-8")


def _connector_by_corp(db: Session, corpid: str) -> AgentIMConnectorORM | None:
    return (db.query(AgentIMConnectorORM)
            .filter(AgentIMConnectorORM.platform == "wecom", AgentIMConnectorORM.app_id == corpid,
                    AgentIMConnectorORM.enabled == 1).first())


async def _access_token(corpid: str, secret: str) -> str | None:
    ck = f"{corpid}:{secret[:6]}"
    c = _token_cache.get(ck)
    if c and c[1] - 60 > time.monotonic():
        return c[0]
    try:
        async with httpx.AsyncClient(timeout=10) as cli:
            r = await cli.get(f"{QYAPI}/gettoken", params={"corpid": corpid, "corpsecret": secret})
        d = r.json()
        tok = d.get("access_token")
        if tok:
            _token_cache[ck] = (tok, time.monotonic() + int(d.get("expires_in", 7000)))
        else:
            log.warning("[wecom] gettoken failed: %s", d)
        return tok
    except Exception as e:  # noqa: BLE001
        log.warning("[wecom] gettoken error: %s", e)
        return None


async def _send_markdown(corpid: str, secret: str, agentid, touser: str, text: str) -> None:
    tok = await _access_token(corpid, secret)
    if not tok:
        log.warning("[wecom] no access_token; cannot reply")
        return
    payload = {"touser": touser, "msgtype": "markdown", "agentid": agentid,
               "markdown": {"content": _to_wecom_md(text)}}
    try:
        async with httpx.AsyncClient(timeout=15) as cli:
            r = await cli.post(f"{QYAPI}/message/send", params={"access_token": tok}, json=payload)
        d = r.json()
        if d.get("errcode") not in (0, None):
            log.warning("[wecom] send message failed errcode=%s errmsg=%s", d.get("errcode"), d.get("errmsg"))
        else:
            log.info("[wecom] replied touser=%s", touser)
    except Exception as e:  # noqa: BLE001
        log.warning("[wecom] send message error: %s", e)


async def _handle(corpid: str, secret: str, agentid, account_id: int, touser: str, text: str) -> None:
    from agents.geo.agent import build_embed_agent, route_line
    from agents.geo.methods import load_message_history, save_message_history

    await _send_markdown(corpid, secret, agentid, touser, "⏳ 正在查询,请稍候…")    # Immediate acknowledgement
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
        await _send_markdown(corpid, secret, agentid, touser, (result.output or "（无输出）").strip())
    except Exception as e:  # noqa: BLE001
        from agents.geo.runtime.limits import public_message
        await _send_markdown(corpid, secret, agentid, touser,
                             public_message(e) or f"抱歉,处理出错了:{e}")
    finally:
        db.close()


@router.get("/wecom/callback")
async def wecom_verify(request: Request):
    """URL verification (GET echostr): verify signature -> decrypt echostr -> return the plaintext as-is."""
    q = request.query_params
    msg_sig, ts, nonce, echostr = q.get("msg_signature", ""), q.get("timestamp", ""), q.get("nonce", ""), q.get("echostr", "")
    db = SessionLocal()
    try:
        # At verification time we don't know which corp it is yet (only in echostr); iterate over wecom connectors and try decrypting (usually few)
        for conn in db.query(AgentIMConnectorORM).filter(AgentIMConnectorORM.platform == "wecom").all():
            if _sign(conn.verify_token, ts, nonce, echostr) == msg_sig:
                try:
                    plain, _ = _decrypt(echostr, conn.aes_key)
                    return PlainTextResponse(plain)
                except Exception:  # noqa: BLE001
                    continue
        log.warning("[wecom] URL verification matched no connector")
        return PlainTextResponse("", status_code=400)
    finally:
        db.close()


@router.post("/wecom/callback")
async def wecom_callback(request: Request, bg: BackgroundTasks):
    q = request.query_params
    msg_sig, ts, nonce = q.get("msg_signature", ""), q.get("timestamp", ""), q.get("nonce", "")
    raw = (await request.body()).decode("utf-8", "ignore")
    try:
        encrypt = ET.fromstring(raw).findtext("Encrypt") or ""
    except ET.ParseError:
        return PlainTextResponse("")
    db = SessionLocal()
    try:
        # Find the connector by verifying the signature with verify_token
        conn = None
        for c in db.query(AgentIMConnectorORM).filter(AgentIMConnectorORM.platform == "wecom", AgentIMConnectorORM.enabled == 1).all():
            if _sign(c.verify_token, ts, nonce, encrypt) == msg_sig:
                conn = c
                break
        if conn is None:
            log.warning("[wecom] message signature matched nothing")
            return PlainTextResponse("")
        msg_xml, _ = _decrypt(encrypt, conn.aes_key)
        node = ET.fromstring(msg_xml)
        if (node.findtext("MsgType") or "") != "text":
            return PlainTextResponse("")
        from_user = node.findtext("FromUserName") or ""
        content = (node.findtext("Content") or "").strip()
        msg_id = node.findtext("MsgId") or ""
        if msg_id and await seen_before(msg_id):
            return PlainTextResponse("")
        if from_user and content:
            if conn.last_chat_id != from_user:           # Record the latest user for proactive push-back
                conn.last_chat_id = from_user
                db.commit()
            try:
                agentid = json.loads(conn.config_json or "{}").get("agentid")
            except json.JSONDecodeError:
                agentid = None
            log.info("[wecom] message corp=%s user=%s text=%r", conn.app_id, from_user, content[:40])
            bg.add_task(_handle, conn.app_id, conn.app_secret, agentid, conn.account_id, from_user, content)
        return PlainTextResponse("")        # Empty string = no passive reply; the answer is pushed asynchronously via message/send
    finally:
        db.close()
