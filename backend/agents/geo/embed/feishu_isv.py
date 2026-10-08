"""Feishu ISV connector (app marketplace app, multi-tenant).

Coexists with the self-built mode (im_feishu.py). Differences:
- One app (ours) serves countless companies; auth uses the ISV three-step flow:
  app_ticket (pushed periodically by Feishu) -> app_access_token -> tenant_access_token (per tenant_key).
- Each user binds individually: (tenant_key, open_id) -> Vigilath account_id; unbound users are prompted to send a "binding code".

Our ISV app credentials come from environment variables (one set of our own, not per customer):
  FEISHU_ISV_APP_ID / FEISHU_ISV_APP_SECRET / FEISHU_ISV_VERIFY_TOKEN / FEISHU_ISV_ENCRYPT_KEY
Callback URL (configured once in the Feishu ISV app): <host>/api/agent/im/feishu-isv/callback
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime

import httpx
from fastapi import APIRouter, BackgroundTasks, Request
from sqlalchemy.orm import Session

from agents.geo.deps import AgentDeps
from agents.geo.embed.im_feishu import _build_card_v2, _has_table, _maybe_decrypt, _to_feishu_md, log
from agents.geo.embed.welcome import welcome_markdown
from geo.database import SessionLocal
from geo.models.agent import AgentIMBindCodeORM, AgentIMUserBindingORM, AgentISVStateORM

router = APIRouter(prefix="/agent/im")
FEISHU_BASE = "https://open.feishu.cn/open-apis"

_app_token_cache: tuple[str, float] | None = None
_tenant_token_cache: dict[str, tuple[str, float]] = {}
_seen_events: dict[str, float] = {}


def _isv() -> tuple[str, str, str, str]:
    return (
        (os.environ.get("FEISHU_ISV_APP_ID") or "").strip(),
        (os.environ.get("FEISHU_ISV_APP_SECRET") or "").strip(),
        (os.environ.get("FEISHU_ISV_VERIFY_TOKEN") or "").strip(),
        (os.environ.get("FEISHU_ISV_ENCRYPT_KEY") or "").strip(),
    )


def _save_app_ticket(db: Session, app_id: str, ticket: str) -> None:
    row = db.query(AgentISVStateORM).filter(AgentISVStateORM.app_id == app_id).first()
    if row is None:
        row = AgentISVStateORM(app_id=app_id)
        db.add(row)
    row.app_ticket = ticket
    row.updated_at = datetime.utcnow()
    db.commit()
    log.info("app_ticket updated (app_id=%s)", app_id)


def _get_app_ticket(db: Session, app_id: str) -> str:
    row = db.query(AgentISVStateORM).filter(AgentISVStateORM.app_id == app_id).first()
    return row.app_ticket if row else ""


async def _resend_app_ticket(app_id: str, app_secret: str) -> None:
    """When there is no app_ticket, actively ask Feishu to re-push it (it POSTs to our callback)."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(f"{FEISHU_BASE}/auth/v3/app_ticket/resend",
                         json={"app_id": app_id, "app_secret": app_secret})
    except Exception:  # noqa: BLE001
        pass


async def _app_access_token(db: Session) -> str | None:
    global _app_token_cache
    if _app_token_cache and _app_token_cache[1] - 60 > time.monotonic():
        return _app_token_cache[0]
    app_id, app_secret, _, _ = _isv()
    ticket = _get_app_ticket(db, app_id)
    if not ticket:
        await _resend_app_ticket(app_id, app_secret)
        log.warning("No app_ticket; requested Feishu to re-push; retry later")
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{FEISHU_BASE}/auth/v3/app_access_token",
                             json={"app_id": app_id, "app_secret": app_secret, "app_ticket": ticket})
        d = r.json()
        tok = d.get("app_access_token")
        if tok:
            _app_token_cache = (tok, time.monotonic() + int(d.get("expire", 7000)))
        else:
            log.warning("Failed to obtain app_access_token: %s", d)
        return tok
    except Exception as e:  # noqa: BLE001
        log.warning("app_access_token error: %s", e)
        return None


async def _tenant_token(db: Session, tenant_key: str) -> str | None:
    cached = _tenant_token_cache.get(tenant_key)
    if cached and cached[1] - 60 > time.monotonic():
        return cached[0]
    app_tok = await _app_access_token(db)
    if not app_tok:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{FEISHU_BASE}/auth/v3/tenant_access_token",
                             json={"app_access_token": app_tok, "tenant_key": tenant_key})
        d = r.json()
        tok = d.get("tenant_access_token")
        if tok:
            _tenant_token_cache[tenant_key] = (tok, time.monotonic() + int(d.get("expire", 7000)))
        return tok
    except Exception as e:  # noqa: BLE001
        log.warning("tenant_access_token error: %s", e)
        return None


async def _send_card(db: Session, tenant_key: str, receive_id: str, text: str,
                     receive_id_type: str = "chat_id") -> None:
    tok = await _tenant_token(db, tenant_key)
    if not tok:
        log.warning("ISV cannot get tenant_access_token (tenant=%s); unable to reply", tenant_key)
        return

    async def _post(card: dict) -> int | None:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(f"{FEISHU_BASE}/im/v1/messages", params={"receive_id_type": receive_id_type},
                                 headers={"Authorization": f"Bearer {tok}"},
                                 json={"receive_id": receive_id, "msg_type": "interactive",
                                       "content": json.dumps(card, ensure_ascii=False)})
            return r.json().get("code")
        except Exception as e:  # noqa: BLE001
            log.warning("ISV send message error: %s", e)
            return None

    # Has tables -> real table card; failure/no tables -> markdown (tables converted to list rows)
    if _has_table(text):
        if await _post(_build_card_v2(text)) == 0:
            log.info("ISV replied (table card) tenant=%s", tenant_key)
            return
        log.warning("ISV table card rejected; falling back to list rows")
    code = await _post({"config": {"wide_screen_mode": True}, "elements": [{"tag": "markdown", "content": _to_feishu_md(text)}]})
    if code not in (0, None):
        log.warning("ISV send message failed code=%s", code)
    elif code == 0:
        log.info("ISV replied tenant=%s to=%s", tenant_key, receive_id)


def _installer_open_id(event: dict) -> str:
    """Get the installer / admin open_id from the app_open (first app activation) event (defensive multi-field lookup, pending calibration on a physical device)."""
    for key in ("installer", "installer_employee", "operator"):
        oid = (event.get(key) or {}).get("open_id")
        if oid:
            return oid
    for a in (event.get("applicants") or []):
        oid = (a or {}).get("open_id")
        if oid:
            return oid
    return ""


async def _send_welcome(tenant_key: str, admin_open_id: str) -> None:
    """After install, push a welcome card to the admin via DM (required by Feishu app marketplace review)."""
    db = SessionLocal()
    try:
        await _send_card(db, tenant_key, admin_open_id, welcome_markdown(), receive_id_type="open_id")
        log.info("ISV welcome card sent tenant=%s admin=%s", tenant_key, admin_open_id)
    except Exception as e:  # noqa: BLE001
        log.warning("ISV welcome card send failed tenant=%s: %s", tenant_key, e)
    finally:
        db.close()


def _try_bind(db: Session, tenant_key: str, text: str) -> int | None:
    """If the text is a valid binding code -> create an **enterprise-level** binding (shared by the whole tenant) and return account_id; otherwise None."""
    from agents.geo.embed.onboard import bind_enterprise

    code = (text or "").strip().upper()
    if not (4 <= len(code) <= 16):
        return None
    row = (db.query(AgentIMBindCodeORM)
           .filter(AgentIMBindCodeORM.code == code, AgentIMBindCodeORM.used == 0,
                   AgentIMBindCodeORM.expires_at > datetime.utcnow()).first())
    if not row:
        return None
    bind_enterprise(db, "feishu", tenant_key, row.account_id)    # open_id="" means enterprise-level, shared by the whole tenant
    row.used = 1
    db.commit()
    log.info("ISV enterprise binding succeeded tenant=%s -> account=%s", tenant_key, row.account_id)
    return row.account_id


async def _handle_message(tenant_key: str, chat_id: str, open_id: str, text: str) -> None:
    from agents.geo.agent import build_embed_agent, route_line
    from agents.geo.embed.onboard import has_active_subscription, onboard_url, resolve_enterprise_account
    from agents.geo.methods import load_message_history, save_message_history

    db = SessionLocal()
    try:
        # Enterprise shared account: resolved by tenant_key (one Vigilath account per tenant), regardless of who sent it
        account_id = resolve_enterprise_account(db, "feishu", tenant_key)
        if account_id is None:
            # Existing customers can send a binding code to bind the enterprise directly; new customers go through the signup + purchase deep link
            account_id = _try_bind(db, tenant_key, text)
            if account_id is not None:
                await _send_card(db, tenant_key, chat_id, "✅ 已开通!贵公司现在可以直接问我「今天舆情怎么样」「帮我诊断」了。")
                return
            link = onboard_url("feishu", tenant_key, _isv()[0])
            await _send_card(db, tenant_key, chat_id,
                             f"👋 贵公司还未开通 Vigilath(AI 搜索可见性 + 舆情监控)。\n"
                             f"[点此注册并选购服务]({link})——开通后本群/单聊即可直接用。\n"
                             f"(已是老客户?登录控制台 → 对接集成 → 获取绑定码发我即可。)")
            return
        if not has_active_subscription(db, account_id):
            link = onboard_url("feishu", tenant_key, _isv()[0])
            await _send_card(db, tenant_key, chat_id,
                             f"贵公司账号当前没有有效套餐,[点此选购/续费]({link})后即可使用全部能力。")
            return
        await _send_card(db, tenant_key, chat_id, "⏳ 正在查询,请稍候…")    # Immediate acknowledgement
        deps = AgentDeps(account_id=account_id, user_id=account_id, db=db, caps=["read", "write"], channel="im")
        agent = build_embed_agent(True, line=route_line(text), channel="im")
        history = load_message_history(deps)
        from agents.geo.runtime.limits import run_guarded, usage_limits_for
        result = await run_guarded(
            agent.run(text, deps=deps, message_history=history,
                      usage_limits=usage_limits_for('im')),
            profile='im')
        try:
            save_message_history(deps, result.all_messages_json())
        except Exception:  # noqa: BLE001
            pass
        await _send_card(db, tenant_key, chat_id, (result.output or "（无输出）").strip())
    except Exception as e:  # noqa: BLE001
        await _send_card(db, tenant_key, chat_id, f"抱歉,处理出错了:{e}")
    finally:
        db.close()


@router.post("/feishu-isv/callback")
async def feishu_isv_callback(request: Request, bg: BackgroundTasks):
    body = await request.json()
    _, _, verify_token, encrypt_key = _isv()
    if encrypt_key and "encrypt" in body:
        body = _maybe_decrypt(body, encrypt_key)

    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge", "")}

    db = SessionLocal()
    try:
        header = body.get("header") or {}
        event = body.get("event") or {}

        # app_ticket push (ISV must handle): may be in event.app_ticket or at the top level
        ticket = event.get("app_ticket") or body.get("app_ticket")
        if ticket:
            app_id = event.get("app_id") or header.get("app_id") or _isv()[0]
            _save_app_ticket(db, app_id, ticket)
            return {"code": 0}

        # Dedup
        eid = header.get("event_id") or body.get("uuid") or ""
        now = time.time()
        if eid:
            for k in [k for k, t in _seen_events.items() if now - t > 600]:
                _seen_events.pop(k, None)
            if eid in _seen_events:
                return {"code": 0}
            _seen_events[eid] = now

        # First app activation -> push a welcome card to the admin (app intro / help docs / service desk / app entry / admin console)
        if header.get("event_type") == "app_open":
            tenant_key = header.get("tenant_key") or event.get("tenant_key") or ""
            admin_open_id = _installer_open_id(event)
            if tenant_key and admin_open_id:
                log.info("ISV app activated tenant=%s admin=%s", tenant_key, admin_open_id)
                bg.add_task(_send_welcome, tenant_key, admin_open_id)
            else:
                log.warning("ISV app activated but admin open_id unavailable tenant=%s event=%s", tenant_key, event)
            return {"code": 0}

        if header.get("event_type") == "im.message.receive_v1":
            msg = event.get("message") or {}
            if msg.get("message_type") == "text":
                import re as _re
                tenant_key = header.get("tenant_key") or ""
                open_id = (((event.get("sender") or {}).get("sender_id") or {}).get("open_id")) or ""
                chat_id = msg.get("chat_id") or ""
                try:
                    text = json.loads(msg.get("content") or "{}").get("text", "").strip()
                except json.JSONDecodeError:
                    text = ""
                text = _re.sub(r"@_user_\d+", "", text).strip()
                # In group chats reply only when @-mentioned (the bot); DMs as usual (ISV uses a lightweight "reply if any @" check for now)
                if (msg.get("chat_type") or "") == "group" and not (msg.get("mentions") or []):
                    return {"code": 0}
                if chat_id and open_id and text:
                    log.info("ISV message tenant=%s open_id=%s text=%r", tenant_key, open_id, text[:40])
                    bg.add_task(_handle_message, tenant_key, chat_id, open_id, text)
        return {"code": 0}
    finally:
        db.close()
