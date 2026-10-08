"""/api/agent/v1/device/* — device code authorization (skill install without copying a token).

Flow (simplified OAuth 2.0 Device Authorization Grant):

    CLI: POST /device/code            → {device_code, user_code, verify_url, interval, expires_in}
    Human: open verify_url in a browser, enter user_code, click 「批准」 ("Approve") while logged in
         (the approval endpoint is on the main backend /api/account/agent-device/*, using the login JWT)
    CLI: POST /device/token (with device_code, once every interval seconds) -> returns the plaintext token after approval

Three deliberate design choices:

1. **No plaintext token stored in DB**: approval only writes account_id; the token is signed on the spot when polling hits approved and immediately marked
   consumed. A device_code can be exchanged for a token only once; even if someone copies the device_code they can't get a second one.
2. **Always HTTP 200 + a status field**, not OAuth's standard 400 + error. Because the consumer is
   `curl` + shell: a 400 makes `curl -f` fail and drop the body, so the script would lose the "still waiting" semantics.
3. **user_code alphabet excludes 0/O/1/I/L**: this code is read aloud and typed by hand.
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from agents.geo.embed.tokens import issue_token
from geo.database import SessionLocal
from geo.models.agent import AgentDeviceCodeORM

router = APIRouter(prefix="/agent/v1/device")

CODE_TTL_SECONDS = 600          # 10 minutes: enough to switch to a browser, log in and enter the code, without leaving a pending approval window open long-term
POLL_INTERVAL = 5               # Suggested polling interval (seconds)
TOKEN_TTL_DAYS = 365
USER_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # Excludes easily confused 0 O 1 I L


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _site_base(request: Request) -> str:
    """Site hosting the approval page.

    Derived from request headers first — the CLI calls in with `--base <site-domain>`, so "whichever domain you install from
    gets that domain's approval page"; test / vigilath.cn / .com.cn all line up automatically without per-site env.
    VIGILATH_SITE_BASE is only an explicit override; if neither is available, fall back to the ICP-registered CN domain.
    """
    env = (os.environ.get("VIGILATH_SITE_BASE") or "").strip()
    if env:
        return env.rstrip("/")
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()
    # The reverse proxy's X-Forwarded-Proto takes priority. Without this header we **cannot** use request.url.scheme directly:
    # the nginx -> uvicorn hop is always http, which would write production links as http://, forcing users through a redirect.
    # So only local addresses are treated as http; everything else is emitted as https.
    xfp = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    if not host:
        return "https://vigilath.cn"
    is_local = host.startswith("127.0.0.1") or host.startswith("localhost") or host.startswith("[::1]")
    proto = xfp or (request.url.scheme if is_local else "https")
    return f"{proto}://{host}"


def _gen_user_code() -> str:
    raw = "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_user_code(s: str) -> str:
    """Hand-typed code: strip spaces / uppercase / add hyphen. The approval side uses this too; both sides must normalize identically."""
    raw = "".join(ch for ch in (s or "").upper() if ch.isalnum())
    return f"{raw[:4]}-{raw[4:8]}" if len(raw) >= 8 else raw


def _client_ip(request: Request) -> str:
    fwd = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return (fwd or (request.client.host if request.client else ""))[:64]


def _purge_expired(db: Session) -> None:
    """Lazy cleanup: delete rows expired for more than 10 minutes. Volume is tiny, not worth a dedicated scheduled job."""
    cutoff = datetime.utcnow() - timedelta(seconds=CODE_TTL_SECONDS)
    db.query(AgentDeviceCodeORM).filter(AgentDeviceCodeORM.expires_at < cutoff).delete(synchronize_session=False)


class DeviceCodeIn(BaseModel):
    client: str = ""      # Requester's self-description (hostname / agent name), only shown to the human on the approval page


@router.post("/code")
async def create_device_code(body: DeviceCodeIn, request: Request, db: Session = Depends(get_db)):
    """CLI starts the flow. No auth (the user has no credentials yet); protected by short TTL + single use + manual approval."""
    _purge_expired(db)

    # user_code has a unique constraint; re-roll on collision (32^8 space, collisions don't really happen; the loop is just a fallback)
    for _ in range(8):
        user_code = _gen_user_code()
        if db.query(AgentDeviceCodeORM).filter(AgentDeviceCodeORM.user_code == user_code).first() is None:
            break
    else:
        return {"status": "error", "message": "生成配对码失败,请重试"}

    now = datetime.utcnow()
    row = AgentDeviceCodeORM(
        device_code=secrets.token_urlsafe(32)[:64],
        user_code=user_code,
        status="pending",
        client=(body.client or "")[:128],
        client_ip=_client_ip(request),
        expires_at=now + timedelta(seconds=CODE_TTL_SECONDS),
        created_at=now,
    )
    db.add(row)
    db.commit()

    site = _site_base(request)
    return {
        "status": "ok",
        "device_code": row.device_code,
        "user_code": row.user_code,
        "verify_url": f"{site}/link",
        "verify_url_complete": f"{site}/link?code={row.user_code}",
        "interval": POLL_INTERVAL,
        "expires_in": CODE_TTL_SECONDS,
    }


class DeviceTokenIn(BaseModel):
    device_code: str


@router.post("/token")
async def poll_device_token(body: DeviceTokenIn, db: Session = Depends(get_db)):
    """CLI polling. status: pending / slow_down / approved (with token) / denied / expired."""
    row = (
        db.query(AgentDeviceCodeORM)
        .filter(AgentDeviceCodeORM.device_code == (body.device_code or ""))
        .first()
    )
    # Unknown and already-consumed device_codes both return expired, without leaking "this code existed"
    if row is None or row.status == "consumed":
        return {"status": "expired", "message": "配对码无效或已使用,请重新安装"}

    now = datetime.utcnow()
    if row.expires_at < now:
        return {"status": "expired", "message": "配对码已过期(10 分钟),请重新安装"}

    # Rate limit: polling faster than the suggested interval returns slow_down, without advancing state or punitively extending
    if row.last_poll_at and (now - row.last_poll_at).total_seconds() < POLL_INTERVAL - 1:
        return {"status": "slow_down", "interval": POLL_INTERVAL}
    row.last_poll_at = now
    db.commit()

    if row.status == "denied":
        return {"status": "denied", "message": "用户拒绝了本次授权"}
    if row.status != "approved" or not row.account_id:
        return {"status": "pending", "interval": POLL_INTERVAL, "user_code": row.user_code}

    # Approved — sign the token now and immediately mark consumed (single use; plaintext only this once)
    token, tid = issue_token(
        db,
        row.account_id,
        caps=["read", "write"],
        label=f"device:{row.client or 'cli'}"[:255],
        ttl_days=TOKEN_TTL_DAYS,
    )
    row.status = "consumed"
    db.commit()
    return {
        "status": "approved",
        "token": token,
        "tid": tid,
        "account_id": row.account_id,
        "caps": ["read", "write"],
        "expires_in_days": TOKEN_TTL_DAYS,
    }
