"""WeCom web app "silent login + enterprise binding" backend — for the dedicated /wecom agent page.

Flow:
  /wecom page (WeCom webview) -> redirect to WeCom OAuth for a code -> POST /wecom-oauth/login {code}
    -> backend calls getuserinfo3rd(code) with suite_access_token -> gets corpid (+userid)
    -> resolve the enterprise account by (wecom, corpid):
        ├ bound   -> issue that account's embed chat_token -> page chats directly via /v1/chat (no login)
        └ unbound -> return bind_token (signed, carries corpid) -> after login/signup the page calls /onboard/bind to bind
                   -> then calls /wecom-oauth/chat-token to get a chat_token and enter chat

Dependencies (must be ready first, otherwise getuserinfo3rd fails):
  - WECOM_SUITE_ID/SECRET must be **the listed suite** (same env set as im_wecom_isv);
  - the service provider server's egress IP is added to the WeCom IP whitelist (otherwise 60020);
  - the trusted domain is verified (OAuth redirect domain).
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from agents.geo.auth import get_current_user
from agents.geo.embed.im_wecom import log
from agents.geo.embed.onboard import issue_onboard_token, resolve_enterprise_account
from agents.geo.embed.tokens import issue_token
from geo.database import SessionLocal
from geo.models.user import UserORM

router = APIRouter(prefix="/agent/im")
QYAPI = "https://qyapi.weixin.qq.com/cgi-bin"


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/wecom-oauth/config")
def wecom_oauth_config():
    """For the frontend to build the WeCom OAuth redirect: returns suite_id (not secret, may be public)."""
    from agents.geo.embed.im_wecom_isv import _suite

    return {"suite_id": _suite()[0]}


class OAuthLoginIn(BaseModel):
    code: str


@router.post("/wecom-oauth/login")
async def wecom_oauth_login(body: OAuthLoginIn, db: Session = Depends(get_db)):
    """WeCom OAuth code -> resolve corpid -> return chat_token if bound / bind_token if unbound."""
    from agents.geo.embed.im_wecom_isv import _suite_token

    stok = await _suite_token(db)
    if not stok:
        raise HTTPException(status_code=503, detail="suite_token 暂不可用(检查 IP 白名单 / suite 凭证 / 是否已授权)")
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{QYAPI}/service/auth/getuserinfo3rd",
                            params={"suite_access_token": stok, "code": body.code})
        d = r.json()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"企微 getuserinfo3rd 异常:{e}")
    corpid = d.get("corpid") or d.get("CorpId") or ""
    userid = d.get("userid") or d.get("UserId") or d.get("open_userid") or ""
    if not corpid:
        raise HTTPException(status_code=400, detail=f"OAuth 取用户失败:{d}")

    account_id = resolve_enterprise_account(db, "wecom", corpid)
    if account_id:
        token, _ = issue_token(db, account_id, caps=["read", "write"], label=f"wecom-oauth:{corpid}", ttl_days=2)
        log.info("[wecom-oauth] silent login hit corp=%s user=%s -> account=%s", corpid, userid[:8], account_id)
        return {"bound": True, "corpid": corpid, "chat_token": token}

    # Unbound: issue a signed bind_token (reuses the onboard token; payload includes platform+tenant_key)
    log.info("[wecom-oauth] unbound company corp=%s; guiding to bind", corpid)
    return {"bound": False, "corpid": corpid, "bind_token": issue_onboard_token("wecom", corpid)}


@router.post("/wecom-oauth/chat-token")
def wecom_oauth_chat_token(current_user: UserORM = Depends(get_current_user), db: Session = Depends(get_db)):
    """A logged-in user (just completed binding) exchanges for this account's embed chat_token, for /v1/chat."""
    token, _ = issue_token(db, current_user.id, caps=["read", "write"], label="wecom-oauth:post-bind", ttl_days=2)
    return {"chat_token": token}
