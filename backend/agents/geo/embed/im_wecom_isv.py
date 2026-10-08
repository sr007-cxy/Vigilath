"""WeCom third-party app (service provider / ISV) connector — one app distributed to countless companies.

Coexists with the self-built mode (im_wecom.py). Differences:
- Auth uses the suite three-step flow: suite_ticket (pushed by WeCom every ~10 minutes) -> suite_access_token -> corp access_token (per authorized company).
- When each company authorizes the install, exchange the AuthCode for a permanent_code and store it; later (suite_token, corpid, permanent_code) is exchanged for corp_token to send messages.
- Each user binds individually: (corpid, userid) -> Vigilath account_id; unbound users are prompted to send a "binding code".

Our suite credentials come from environment variables (one set of our own, not per customer):
  WECOM_SUITE_ID / WECOM_SUITE_SECRET / WECOM_SUITE_TOKEN / WECOM_SUITE_AESKEY
Callback URLs (configured once in the WeCom service provider console):
  Command callback URL <host>/api/agent/im/wecom-isv/cmd     — suite_ticket + company authorize/cancel events
  Data callback URL    <host>/api/agent/im/wecom-isv/data    — user messages from each authorized company

⚠️ This connector hasn't been integration-tested end to end against a real WeCom service provider environment; field assumptions (ToUserName=corpid etc.) follow the official docs.
   First use requires full suite credentials + one company authorizing the install for live calibration.
"""
from __future__ import annotations

import json
import os
import time
import xml.etree.ElementTree as ET
from datetime import datetime

import httpx
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from agents.geo.deps import AgentDeps
from agents.geo.embed.dedup import seen_before
from agents.geo.embed.im_wecom import _decrypt, _sign, _to_wecom_md, log
from agents.geo.embed.welcome import welcome_markdown
from geo.database import SessionLocal
from geo.models.agent import AgentIMBindCodeORM, AgentIMConnectorORM, AgentIMUserBindingORM, AgentISVStateORM

router = APIRouter(prefix="/agent/im")
QYAPI = "https://qyapi.weixin.qq.com/cgi-bin"

_suite_token_cache: tuple[str, float] | None = None
_corp_token_cache: dict[str, tuple[str, float]] = {}


def _suite() -> tuple[str, str, str, str]:
    return (
        (os.environ.get("WECOM_SUITE_ID") or "").strip(),
        (os.environ.get("WECOM_SUITE_SECRET") or "").strip(),
        (os.environ.get("WECOM_SUITE_TOKEN") or "").strip(),
        (os.environ.get("WECOM_SUITE_AESKEY") or "").strip(),
    )


# ── suite_ticket (pushed periodically by WeCom, stored in agent_isv_state, app_id=suite_id) ──────────
def _save_suite_ticket(db: Session, suite_id: str, ticket: str) -> None:
    row = db.query(AgentISVStateORM).filter(AgentISVStateORM.app_id == suite_id).first()
    if row is None:
        row = AgentISVStateORM(app_id=suite_id)
        db.add(row)
    row.app_ticket = ticket
    row.updated_at = datetime.utcnow()
    db.commit()
    log.info("[wecom-isv] suite_ticket updated suite=%s", suite_id)


def _get_suite_ticket(db: Session, suite_id: str) -> str:
    row = db.query(AgentISVStateORM).filter(AgentISVStateORM.app_id == suite_id).first()
    return row.app_ticket if row else ""


async def _suite_token(db: Session) -> str | None:
    global _suite_token_cache
    if _suite_token_cache and _suite_token_cache[1] - 60 > time.monotonic():
        return _suite_token_cache[0]
    sid, ssecret, _, _ = _suite()
    ticket = _get_suite_ticket(db, sid)
    if not ticket:
        log.warning("[wecom-isv] no suite_ticket yet (wait for WeCom push / check command callback); cannot obtain suite_token")
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{QYAPI}/service/get_suite_token",
                             json={"suite_id": sid, "suite_secret": ssecret, "suite_ticket": ticket})
        d = r.json()
        tok = d.get("suite_access_token")
        if tok:
            _suite_token_cache = (tok, time.monotonic() + int(d.get("expires_in", 7000)))
        else:
            log.warning("[wecom-isv] failed to obtain suite_token: %s", d)
        return tok
    except Exception as e:  # noqa: BLE001
        log.warning("[wecom-isv] suite_token error: %s", e)
        return None


# ── Company authorization info (corpid -> permanent_code + agentid), reusing the connector table platform='wecom_isv' ──
def _save_corp_auth(db: Session, corpid: str, permanent_code: str, agentid, suite_id: str) -> None:
    row = (db.query(AgentIMConnectorORM)
           .filter(AgentIMConnectorORM.platform == "wecom_isv", AgentIMConnectorORM.app_id == corpid).first())
    if row is None:
        row = AgentIMConnectorORM(platform="wecom_isv", app_id=corpid, account_id=0)
        db.add(row)
    row.app_secret = permanent_code                          # Borrow the app_secret field to store permanent_code
    row.config_json = json.dumps({"suite_id": suite_id, "agentid": agentid}, ensure_ascii=False)
    row.enabled = 1
    db.commit()
    log.info("[wecom-isv] company authorization saved corp=%s agentid=%s", corpid, agentid)


def _get_corp_auth(db: Session, corpid: str) -> AgentIMConnectorORM | None:
    return (db.query(AgentIMConnectorORM)
            .filter(AgentIMConnectorORM.platform == "wecom_isv", AgentIMConnectorORM.app_id == corpid,
                    AgentIMConnectorORM.enabled == 1).first())


async def _corp_token(db: Session, corpid: str) -> str | None:
    cached = _corp_token_cache.get(corpid)
    if cached and cached[1] - 60 > time.monotonic():
        return cached[0]
    auth = _get_corp_auth(db, corpid)
    if not auth or not auth.app_secret:
        return None
    stok = await _suite_token(db)
    if not stok:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{QYAPI}/service/get_corp_token", params={"suite_access_token": stok},
                             json={"auth_corpid": corpid, "permanent_code": auth.app_secret})
        d = r.json()
        tok = d.get("access_token")
        if tok:
            _corp_token_cache[corpid] = (tok, time.monotonic() + int(d.get("expires_in", 7000)))
        else:
            log.warning("[wecom-isv] failed to obtain corp_token corp=%s: %s", corpid, d)
        return tok
    except Exception as e:  # noqa: BLE001
        log.warning("[wecom-isv] corp_token error: %s", e)
        return None


async def _exchange_permanent_code(db: Session, auth_code: str, send_welcome: bool = False) -> None:
    """Company authorization event: exchange auth_code for permanent_code + corpid + agentid and store them.
    When send_welcome=True (only on first install via create_auth), push a welcome card to the installing admin."""
    stok = await _suite_token(db)
    if not stok:
        log.warning("[wecom-isv] no suite_token; cannot obtain permanent_code")
        return
    sid = _suite()[0]
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{QYAPI}/service/get_permanent_code", params={"suite_access_token": stok},
                             json={"auth_code": auth_code})
        d = r.json()
        corpid = ((d.get("auth_corp_info") or {}).get("corpid")) or ""
        permanent_code = d.get("permanent_code") or ""
        agents = ((d.get("auth_info") or {}).get("agent")) or []
        agentid = agents[0].get("agentid") if agents else None
        if corpid and permanent_code:
            _save_corp_auth(db, corpid, permanent_code, agentid, sid)
            if send_welcome:
                # auth_user_info.userid = the admin who installed the app; already in the response, no need to call get_admin_list
                admin_userid = ((d.get("auth_user_info") or {}).get("userid")) or ""
                if admin_userid:
                    try:
                        await _send_markdown(db, corpid, agentid, admin_userid, welcome_markdown())
                        log.info("[wecom-isv] welcome card sent corp=%s admin=%s", corpid, admin_userid)
                    except Exception as e:  # noqa: BLE001
                        log.warning("[wecom-isv] welcome card send failed corp=%s: %s", corpid, e)
                else:
                    log.warning("[wecom-isv] install succeeded but auth_user_info has no userid corp=%s", corpid)
        else:
            log.warning("[wecom-isv] get_permanent_code returned an error: %s", d)
    except Exception as e:  # noqa: BLE001
        log.warning("[wecom-isv] obtain permanent_code error: %s", e)


async def _send_markdown(db: Session, corpid: str, agentid, touser: str, text: str) -> None:
    tok = await _corp_token(db, corpid)
    if not tok:
        log.warning("[wecom-isv] cannot get corp_token (corp=%s); unable to reply", corpid)
        return
    payload = {"touser": touser, "msgtype": "markdown", "agentid": agentid,
               "markdown": {"content": _to_wecom_md(text)}}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{QYAPI}/message/send", params={"access_token": tok}, json=payload)
        d = r.json()
        if d.get("errcode") not in (0, None):
            log.warning("[wecom-isv] send message failed errcode=%s errmsg=%s", d.get("errcode"), d.get("errmsg"))
        else:
            log.info("[wecom-isv] replied corp=%s touser=%s", corpid, touser)
    except Exception as e:  # noqa: BLE001
        log.warning("[wecom-isv] send message error: %s", e)


def _try_bind(db: Session, corpid: str, text: str) -> int | None:
    """If the text is a valid binding code -> create an **enterprise-level** binding (shared by the whole corp) and return account_id; otherwise None."""
    from agents.geo.embed.onboard import bind_enterprise

    code = (text or "").strip().upper()
    if not (4 <= len(code) <= 16):
        return None
    row = (db.query(AgentIMBindCodeORM)
           .filter(AgentIMBindCodeORM.code == code, AgentIMBindCodeORM.used == 0,
                   AgentIMBindCodeORM.expires_at > datetime.utcnow()).first())
    if not row:
        return None
    bind_enterprise(db, "wecom", corpid, row.account_id)        # open_id="" means enterprise-level, shared by the whole corp
    row.used = 1
    db.commit()
    log.info("[wecom-isv] enterprise binding succeeded corp=%s -> account=%s", corpid, row.account_id)
    return row.account_id


async def _handle_message(corpid: str, userid: str, text: str) -> None:
    from agents.geo.agent import build_embed_agent, route_line
    from agents.geo.embed.onboard import has_active_subscription, onboard_url, resolve_enterprise_account
    from agents.geo.methods import load_message_history, save_message_history

    db = SessionLocal()
    try:
        auth = _get_corp_auth(db, corpid)
        agentid = None
        if auth:
            try:
                agentid = json.loads(auth.config_json or "{}").get("agentid")
            except json.JSONDecodeError:
                agentid = None
        # Enterprise shared account: resolved by corpid (one Vigilath account per corp), regardless of who sent it
        account_id = resolve_enterprise_account(db, "wecom", corpid)
        if account_id is None:
            account_id = _try_bind(db, corpid, text)            # Existing customer binding code -> enterprise binding
            if account_id is not None:
                await _send_markdown(db, corpid, agentid, userid, "✅ 已开通!贵公司现在可以直接问我「今天舆情怎么样」「帮我诊断」了。")
                return
            link = onboard_url("wecom", corpid)
            await _send_markdown(db, corpid, agentid, userid,
                                 f"👋 贵公司还未开通 Vigilath(AI 搜索可见性 + 舆情监控)。\n"
                                 f"[点此注册并选购服务]({link})——开通后本应用即可直接用。\n"
                                 f"(已是老客户?登录控制台 → 对接集成 → 获取绑定码发我即可。)")
            return
        if not has_active_subscription(db, account_id):
            link = onboard_url("wecom", corpid)
            await _send_markdown(db, corpid, agentid, userid,
                                 f"贵公司账号当前没有有效套餐,[点此选购/续费]({link})后即可使用全部能力。")
            return
        await _send_markdown(db, corpid, agentid, userid, "⏳ 正在查询,请稍候…")
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
        await _send_markdown(db, corpid, agentid, userid, (result.output or "（无输出）").strip())
    except Exception as e:  # noqa: BLE001
        from agents.geo.runtime.limits import public_message
        await _send_markdown(db, corpid, None, userid,
                             public_message(e) or f"抱歉,处理出错了:{e}")
    finally:
        db.close()


def _verify_echo(suite_token: str, suite_aeskey: str, q) -> PlainTextResponse:
    """GET URL verification shared by the command/data callbacks: verify signature -> decrypt echostr -> return the plaintext as-is."""
    msg_sig, ts, nonce, echostr = (q.get("msg_signature", ""), q.get("timestamp", ""),
                                   q.get("nonce", ""), q.get("echostr", ""))
    if _sign(suite_token, ts, nonce, echostr) != msg_sig:
        log.warning("[wecom-isv] URL verification signature check failed")
        return PlainTextResponse("", status_code=400)
    try:
        plain, _ = _decrypt(echostr, suite_aeskey)
        return PlainTextResponse(plain)
    except Exception as e:  # noqa: BLE001
        log.warning("[wecom-isv] echostr decryption failed: %s", e)
        return PlainTextResponse("", status_code=400)


def _decrypt_body(suite_token: str, suite_aeskey: str, q, raw: str) -> ET.Element | None:
    """POST callback: take Encrypt -> verify signature -> decrypt -> return the plaintext XML root node (None on failure)."""
    try:
        encrypt = ET.fromstring(raw).findtext("Encrypt") or ""
    except ET.ParseError:
        return None
    if _sign(suite_token, q.get("timestamp", ""), q.get("nonce", ""), encrypt) != q.get("msg_signature", ""):
        log.warning("[wecom-isv] callback signature matched nothing")
        return None
    try:
        plain, _ = _decrypt(encrypt, suite_aeskey)
        return ET.fromstring(plain)
    except Exception as e:  # noqa: BLE001
        log.warning("[wecom-isv] callback decryption failed: %s", e)
        return None


@router.get("/wecom-isv/cmd")
async def wecom_isv_cmd_verify(request: Request):
    _, _, token, aeskey = _suite()
    return _verify_echo(token, aeskey, request.query_params)


@router.post("/wecom-isv/cmd")
async def wecom_isv_cmd(request: Request, bg: BackgroundTasks):
    """Command callback: suite_ticket (periodic push) + company authorize / cancel-authorization events."""
    _, _, token, aeskey = _suite()
    node = _decrypt_body(token, aeskey, request.query_params, (await request.body()).decode("utf-8", "ignore"))
    if node is None:
        return PlainTextResponse("")
    info_type = node.findtext("InfoType") or ""
    db = SessionLocal()
    try:
        if info_type == "suite_ticket":
            ticket = node.findtext("SuiteTicket") or ""
            sid = node.findtext("SuiteId") or _suite()[0]
            if ticket:
                _save_suite_ticket(db, sid, ticket)
        elif info_type in ("create_auth", "change_auth"):
            auth_code = node.findtext("AuthCode") or ""
            if auth_code:
                # Only the first install (create_auth) pushes a welcome card; change_auth (authorization change) doesn't bother them again
                bg.add_task(_exchange_permanent_code_bg, auth_code, info_type == "create_auth")
        elif info_type in ("cancel_auth",):
            corpid = node.findtext("AuthCorpId") or ""
            if corpid:
                conn = _get_corp_auth(db, corpid)
                if conn:
                    conn.enabled = 0
                    db.commit()
                    log.info("[wecom-isv] company cancelled authorization corp=%s", corpid)
        return PlainTextResponse("success")
    finally:
        db.close()


async def _exchange_permanent_code_bg(auth_code: str, send_welcome: bool = False) -> None:
    db = SessionLocal()
    try:
        await _exchange_permanent_code(db, auth_code, send_welcome)
    finally:
        db.close()


@router.get("/wecom-isv/data")
async def wecom_isv_data_verify(request: Request):
    _, _, token, aeskey = _suite()
    return _verify_echo(token, aeskey, request.query_params)


@router.post("/wecom-isv/data")
async def wecom_isv_data(request: Request, bg: BackgroundTasks):
    """Data callback: user messages from each authorized company. Only text is handled."""
    _, _, token, aeskey = _suite()
    node = _decrypt_body(token, aeskey, request.query_params, (await request.body()).decode("utf-8", "ignore"))
    if node is None:
        return PlainTextResponse("")
    if (node.findtext("MsgType") or "") != "text":
        return PlainTextResponse("")
    corpid = node.findtext("ToUserName") or ""          # Third-party: ToUserName = authorized company's corpid
    userid = node.findtext("FromUserName") or ""
    content = (node.findtext("Content") or "").strip()
    msg_id = node.findtext("MsgId") or ""
    if msg_id and await seen_before(f"wecomisv:{msg_id}"):
        return PlainTextResponse("")
    if corpid and userid and content:
        log.info("[wecom-isv] message corp=%s user=%s text=%r", corpid, userid, content[:40])
        bg.add_task(_handle_message, corpid, userid, content)
    return PlainTextResponse("")        # Empty string = no passive reply; the answer is pushed asynchronously via message/send
