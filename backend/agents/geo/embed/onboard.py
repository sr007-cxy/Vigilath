"""IM acquisition onboarding — guides unregistered/non-activated IM users to sign up + purchase, auto-binding the enterprise account after payment.

Account model: **enterprise shared**. tenant_key (Feishu tenant / WeCom corpid) -> one Vigilath account,
all IM users in the company share the same brand data. Bindings are stored in AgentIMUserBinding; **open_id="" means an enterprise-level binding** (whole tenant).

Acquisition loop (signed deep link):
  Bot (when not activated) sends onboard_url -> user signs up/logs in -> picks a plan and pays -> frontend calls with the token
  POST /api/agent/im/onboard/bind -> verify token -> create the tenant_key -> account enterprise binding.
"""
from __future__ import annotations

import os
import time

from fastapi import APIRouter, Depends, HTTPException
import jwt
from jwt import PyJWTError as JWTError
from pydantic import BaseModel
from sqlalchemy.orm import Session

from agents.geo.auth import get_current_user
from geo.database import SessionLocal, settings
from geo.models.agent import AgentIMUserBindingORM
from geo.models.user import UserORM

router = APIRouter(prefix="/agent/im")
APP_BASE_URL = (os.environ.get("AGENT_APP_BASE_URL") or "https://www.vigilath.com").rstrip("/")
_ALG = "HS256"
_AUD = "agent-onboard"          # Hard-isolated from the aud of login JWTs / embed tokens
_ISS = "vigilath-agent"


def _secret() -> str:
    # Reuse embed's separate secret (derived from the main SECRET_KEY if not configured separately); isolated from other tokens via aud.
    return (os.environ.get("AGENT_EMBED_SECRET") or "").strip() or f"{settings.SECRET_KEY}::agent-embed"


def issue_onboard_token(platform: str, tenant_key: str, app_id: str = "", ttl_days: int = 7) -> str:
    now = int(time.time())
    payload = {"iss": _ISS, "aud": _AUD, "platform": platform, "tenant_key": tenant_key,
               "app_id": app_id, "iat": now, "exp": now + ttl_days * 86400}
    return jwt.encode(payload, _secret(), algorithm=_ALG)


def verify_onboard_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, _secret(), algorithms=[_ALG], audience=_AUD, issuer=_ISS)
    except JWTError:
        return None


def onboard_url(platform: str, tenant_key: str, app_id: str = "") -> str:
    """Signup + purchase deep link the bot sends to non-activated companies (carries a signed identity, auto-binds after payment)."""
    return f"{APP_BASE_URL}/onboard?t={issue_onboard_token(platform, tenant_key, app_id)}"


# ── Enterprise-level binding / resolution / subscription check (reused by all IM connectors) ─────────────
def bind_enterprise(db: Session, platform: str, tenant_key: str, account_id: int) -> None:
    """Enterprise-level binding: (platform, tenant_key, open_id="") -> account_id. Rebinds if it already exists."""
    row = (db.query(AgentIMUserBindingORM)
           .filter(AgentIMUserBindingORM.platform == platform,
                   AgentIMUserBindingORM.tenant_key == tenant_key,
                   AgentIMUserBindingORM.open_id == "").first())
    if row is None:
        db.add(AgentIMUserBindingORM(platform=platform, tenant_key=tenant_key, open_id="", account_id=account_id))
    else:
        row.account_id = account_id
    db.commit()


def resolve_enterprise_account(db: Session, platform: str, tenant_key: str) -> int | None:
    """Enterprise shared account resolution: fetch the enterprise-level binding (open_id="") by (platform, tenant_key)."""
    row = (db.query(AgentIMUserBindingORM)
           .filter(AgentIMUserBindingORM.platform == platform,
                   AgentIMUserBindingORM.tenant_key == tenant_key,
                   AgentIMUserBindingORM.open_id == "").first())
    return row.account_id if row else None


def has_active_subscription(db: Session, account_id: int) -> bool:
    """Whether the account has a valid plan (is_active and not expired). If not, the bot guides them to purchase."""
    from datetime import datetime

    from geo.models.membership import UserMembershipORM

    return (db.query(UserMembershipORM)
            .filter(UserMembershipORM.user_id == account_id,
                    UserMembershipORM.is_active.is_(True),
                    UserMembershipORM.end_date > datetime.utcnow()).first() is not None)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class OnboardBindIn(BaseModel):
    token: str


@router.post("/onboard/bind")
async def onboard_bind(
    body: OnboardBindIn,
    current_user: UserORM = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """A logged-in user (registered, usually just completed a purchase) completes enterprise binding with the deep-link token: tenant_key -> this account.

    Called by the frontend /onboard after the user logs in + completes purchase; the token comes from ?t= in the landing page URL.
    """
    claims = verify_onboard_token(body.token)
    if not claims:
        raise HTTPException(status_code=400, detail="绑定链接无效或已过期,请在机器人里重新获取")
    platform = claims.get("platform") or "feishu"
    tenant_key = (claims.get("tenant_key") or "").strip()
    if not tenant_key:
        raise HTTPException(status_code=400, detail="绑定信息缺失")
    bind_enterprise(db, platform, tenant_key, current_user.id)
    return {"ok": True, "platform": platform, "tenant_key": tenant_key, "account_id": current_user.id}
