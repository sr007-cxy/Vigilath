"""WeChat QR login (follow the official account → bind phone number).

Frontend sequence:
    GET  /api/auth/wechat/config          # whether the channel is enabled, deciding whether to render the QR tab
    POST /api/auth/wechat/qrcode          # issue a code, get scene
    GET  /api/auth/wechat/qrcode/{scene}  # poll (every 1.5s)
         → pending      waiting for scan
         → need_phone   followed; returns bind_token, frontend enters the phone number step
         → confirmed    existing user already bound a phone number, returns access_token directly
         → expired      expired; frontend refreshes the code
    POST /api/auth/sms/send               # send a verification code with bind_token
    POST /api/auth/wechat/bind-phone      # exchange bind_token + code for access_token

    GET/POST /api/auth/wechat/callback    # the official account's "server configuration" URL, called by WeChat

H5 single-device sequence (field promotion: only one phone on site, so the two-device flow above does not work):
    scan and follow → tap the official account's bottom menu → landing page /g
    GET  /api/auth/wechat/h5/authorize    # get the silent authorization redirect URL (snsapi_base)
         → WeChat returns to the landing page with code
    POST /api/auth/wechat/h5/session      # exchange code for openid, identify the user
         → authorized   existing user already bound a phone number, returns access_token directly (zero clicks throughout)
         → need_phone   returns bind_token, frontend enters the phone number step
    ↓ then reuses the same set of endpoints above
    POST /api/auth/sms/send  +  POST /api/auth/wechat/bind-phone

    This flow **does not use scene**: web authorization hands the openid directly to the landing page, with no need
    for a "backend receives event → frontend polls" relay. The scene in bind_token is stored as an empty string,
    and bind-phone already has an `if scene:` emptiness check, so nothing changes there.

"Following" plays different roles in the two flows; do not mix them up:
  - QR channel: WeChat only pushes the subscribe event after following; someone who has not followed only sees the follow page when scanning,
    the backend receives no event, and scene stays pending — following is the flow's natural threshold.
  - H5 channel: snsapi_base **works for non-followers too** and yields an openid. So following is not a
    technical threshold, just an outcome we want (to push reports later). Requiring it would need an explicit subscribe check,
    and requiring a follow inside WeChat is a bad experience (users cannot scan a code on their own screen), so by default it is not required, only softly encouraged.
"""

import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Request, Response
import jwt
from jwt import PyJWTError as JWTError
from pydantic import BaseModel

from geo.database import SessionLocal, settings
from geo.models.user import Token
from geo.models.wechat_auth import WechatLoginSessionORM
from geo.services import sms_service, wechat_mp_service
from geo.services.user_service import user_service
from geo.utils.error_handler import AppException

logger = logging.getLogger("geo.wechat_auth")

router = APIRouter()

SECRET_KEY = settings.SECRET_KEY
ALGORITHM = "HS256"
BIND_TOKEN_EXPIRE_MINUTES = 15
# Consistent with ACCESS_TOKEN_EXPIRE_MINUTES in auth.py (7 days)
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7


class QrcodeOut(BaseModel):
    scene: str
    qr_url: str
    expires_in: int


class SendSmsIn(BaseModel):
    bind_token: str
    phone: str


class BindPhoneIn(BaseModel):
    bind_token: str
    phone: str
    code: str


class H5SessionIn(BaseModel):
    """Web authorization code received by the landing page. Valid for five minutes and single use."""
    code: str


def _client_ip(request: Request) -> Optional[str]:
    # nginx reverse proxy sits in front; reading request.client directly would only give 127.0.0.1
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else None


def _issue_access_token(email: str) -> str:
    return jwt.encode(
        {"sub": email, "exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)},
        SECRET_KEY, algorithm=ALGORITHM,
    )


def _issue_bind_token(scene: str, openid: str, unionid: Optional[str]) -> str:
    return jwt.encode(
        {
            "typ": "wx_bind",
            "scene": scene,
            "openid": openid,
            "unionid": unionid or "",
            "exp": datetime.utcnow() + timedelta(minutes=BIND_TOKEN_EXPIRE_MINUTES),
        },
        SECRET_KEY, algorithm=ALGORITHM,
    )


def _decode_bind_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise AppException(status_code=401, message="绑定凭证已失效,请重新扫码")
    if payload.get("typ") != "wx_bind" or not payload.get("openid"):
        raise AppException(status_code=401, message="绑定凭证无效,请重新扫码")
    return payload


# ── Config probe ───────────────────────────────────────────────────────────────

@router.get("/wechat/config")
async def wechat_login_config():
    """The frontend uses this to decide whether to render the "WeChat QR" tab and whether to hide the email registration entry."""
    sms_ok = sms_service.is_configured() or settings.SMS_DEBUG_ECHO
    return {
        "wechat_login_enabled": wechat_mp_service.is_configured(),
        "sms_enabled": sms_ok,
        # Phone login only counts as enabled when the SMS channel really works — otherwise the rendered tab would 503 when clicked
        "phone_login_enabled": settings.PHONE_LOGIN_ENABLED and sms_ok,
        "email_register_enabled": settings.EMAIL_REGISTER_ENABLED,
        # H5 single-device channel (field-promotion landing page). Only enabled when the landing base URL is configured — without it
        # redirect_uri cannot be built and the authorization URL is useless; better to let the frontend fall back to phone login.
        "wechat_h5_enabled": bool(wechat_mp_service.is_configured() and _h5_redirect_uri()),
    }


# ── H5 single-device channel (field promotion) ────────────────────────────────────────────────────

def _h5_redirect_uri() -> str:
    """Landing page redirect URL. **Built by the backend, not accepted from the frontend** —

    letting the frontend pass redirect_uri would open an open redirect: anyone could build a
    `/wechat/h5/authorize?redirect_uri=<phishing site>` link and use our official account to
    vouch for someone else's site. WeChat also checks a domain whitelist, but that is someone else's gate,
    and should not be our only one. To carry parameters use `state` (returned by WeChat as-is).
    """
    base = (settings.WECHAT_H5_BASE_URL or settings.FRONTEND_URL or "").strip().rstrip("/")
    if not base:
        return ""
    return f"{base}{settings.WECHAT_H5_LANDING_PATH}"


@router.get("/wechat/h5/authorize")
async def h5_authorize(path: str = "", state: str = ""):
    """Give the landing page a silent authorization redirect URL.

    The return destination = **the page** that started authorization (frontend passes path), not a fixed home page — saving one
    "authorize back to home, then home redirects back to the original page" hop, so users see fewer address bar jumps.

    When enabled=false the frontend must **not redirect** — redirecting to authorization in a browser outside WeChat shows the user
    WeChat's "please open the link in the WeChat client" error page, effectively a blank screen. Fall back to phone SMS login.
    """
    base = (settings.WECHAT_H5_BASE_URL or settings.FRONTEND_URL or "").strip().rstrip("/")
    if not (wechat_mp_service.is_configured() and base):
        return {"enabled": False, "oauth_url": ""}
    # The return only allows **on-site absolute paths** (blocks open redirects): must start with a single /, not //, no scheme,
    # no backslash. Anything invalid falls back to the home page. base is the server-fixed site domain, so the result can only land on this site.
    rp = path or "/"
    if (not rp.startswith("/")) or rp.startswith("//") or "://" in rp or "\\" in rp:
        rp = "/"
    redirect_uri = f"{base}{rp}"
    # WeChat limits state to 128 bytes, and exceeding it fails the whole redirect; truncate here instead of erroring
    return {
        "enabled": True,
        "oauth_url": wechat_mp_service.build_oauth_url(redirect_uri, (state or "")[:128]),
    }


@router.post("/wechat/h5/session")
async def h5_session(body: H5SessionIn):
    """Exchange code for openid, then identify the user.

    Two outcomes:
      - `authorized` — this WeChat already has an account with a bound phone number; issue a JWT directly.
        Returning users get zero clicks throughout the second time, the biggest value of this flow.
      - `need_phone` — issue a bind_token; the frontend enters the phone number step (reusing the two PC-flow endpoints).

    `subscribed` is tri-state: True followed / False not followed / None **could not fetch**.
    None is not "not followed" — `cgi-bin/user/info` uses the global access_token, subject to the official account's
    IP whitelist (40164); when the deploy machine is not whitelisted this is None. The frontend must not show
    "you have not followed yet" for None, or it will misreport to a room full of followers.
    """
    if not wechat_mp_service.is_configured():
        raise AppException(status_code=503, message="微信登录暂未开通")

    try:
        ident = wechat_mp_service.exchange_code_for_openid(body.code)
    except wechat_mp_service.WechatMpNotConfigured as exc:
        # Most commonly 40163: the user refreshed the page carrying the code, using the same code a second time
        logger.warning("H5 web authorization openid exchange failed: %s", exc)
        raise AppException(status_code=401, message="微信授权已失效,请从公众号菜单重新进入")

    openid = ident["openid"]
    unionid = ident.get("unionid")
    profile = wechat_mp_service.fetch_user_info(openid)
    unionid = unionid or profile.get("unionid")
    raw_sub = profile.get("subscribe")
    subscribed = None if raw_sub is None else bool(raw_sub)

    user = user_service.get_user_by_wechat(openid, unionid)
    if user and user.phone:
        # Write back follow status truthfully: without writing back, users who unfollowed stay True forever, and later
        # reports pushed to "followers" fail with no way to find out why
        if subscribed is not None and bool(user.wechat_subscribed) != subscribed:
            user_service.set_wechat_subscribed(openid, subscribed)
        return {
            "status": "authorized",
            "access_token": _issue_access_token(user.email),
            "token_type": "bearer",
            "subscribed": subscribed,
        }

    return {
        "status": "need_phone",
        # scene is an empty string: this flow has no QR session, so `if scene:` in bind-phone skips the write-back
        "bind_token": _issue_bind_token("", openid, unionid),
        "subscribed": subscribed,
    }


# ── Issue code / poll ────────────────────────────────────────────────────────────

@router.post("/wechat/qrcode", response_model=QrcodeOut)
async def create_qrcode(request: Request):
    if not wechat_mp_service.is_configured():
        raise AppException(status_code=503, message="微信扫码登录暂未开通")

    scene = secrets.token_urlsafe(24)[:48]
    expire = settings.WECHAT_QR_EXPIRE_SECONDS
    try:
        qr = wechat_mp_service.create_login_qrcode(scene, expire)
    except wechat_mp_service.WechatMpNotConfigured as exc:
        logger.error("Failed to generate login QR code: %s", exc)
        raise AppException(status_code=503, message="微信扫码登录暂不可用,请稍后再试")

    now = datetime.utcnow()
    db = SessionLocal()
    try:
        db.add(WechatLoginSessionORM(
            scene=scene,
            ticket=qr["ticket"],
            status="pending",
            client_ip=_client_ip(request),
            created_at=now,
            expires_at=now + timedelta(seconds=qr["expire_seconds"]),
        ))
        db.commit()
    finally:
        db.close()

    return QrcodeOut(scene=scene, qr_url=qr["qr_url"], expires_in=qr["expire_seconds"])


@router.get("/wechat/qrcode/{scene}")
async def poll_qrcode(scene: str):
    now = datetime.utcnow()
    db = SessionLocal()
    try:
        row = db.query(WechatLoginSessionORM).filter(WechatLoginSessionORM.scene == scene).first()
        if not row:
            raise AppException(status_code=404, message="二维码不存在,请刷新")

        if row.status in ("pending",) and row.expires_at <= now:
            row.status = "expired"
            db.commit()

        if row.status == "expired":
            return {"status": "expired"}
        if row.status == "consumed":
            # One-time: once exchanged for a JWT the token is not returned again, preventing replay of the poll response
            return {"status": "consumed"}
        if row.status == "pending":
            return {"status": "pending"}

        if row.status == "need_phone":
            return {"status": "need_phone", "bind_token": row.bind_token}

        # confirmed — existing user with a bound phone number, issue a JWT directly
        user = user_service.get_user_by_id(row.user_id) if row.user_id else None
        if not user:
            return {"status": "pending"}
        row.status = "consumed"
        row.consumed_at = now
        db.commit()
        return {
            "status": "confirmed",
            "access_token": _issue_access_token(user.email),
            "token_type": "bearer",
        }
    finally:
        db.close()


# ── Phone number binding ─────────────────────────────────────────────────────────────

@router.post("/sms/send")
async def send_sms_code(body: SendSmsIn, request: Request):
    """Send a verification code. A bind_token from QR scan/authorization is required first — otherwise this is a free SMS cannon."""
    payload = _decode_bind_token(body.bind_token)

    phone = sms_service.normalize_phone(body.phone)
    if not phone:
        raise AppException(status_code=400, message="请输入正确的手机号")

    try:
        # Rate-limit key switched to openid: everyone at a field-promotion booth shares one hotspot egress IP, and per-IP limits
        # (8 per hour) would block everyone collectively from the 9th person on, with nobody on site able to tell why.
        # openid is a strong identity only obtainable after WeChat authorization, more trustworthy than IP.
        sms_service.send_code(phone, _client_ip(request), purpose="bind",
                              identity=payload.get("openid") or None)
    except sms_service.SmsNotConfigured:
        raise AppException(status_code=503, message="短信服务暂未开通")
    except sms_service.SmsRateLimited as exc:
        raise AppException(status_code=429, message=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to send verification code phone=%s: %s", phone, exc)
        raise AppException(status_code=502, message="验证码发送失败,请稍后重试")

    return {"sent": True, "resend_after": settings.SMS_RESEND_INTERVAL_SECONDS}


@router.post("/wechat/bind-phone", response_model=Token)
async def bind_phone(body: BindPhoneIn):
    payload = _decode_bind_token(body.bind_token)
    openid = payload["openid"]
    unionid = payload.get("unionid") or None
    scene = payload.get("scene")

    phone = sms_service.normalize_phone(body.phone)
    if not phone:
        raise AppException(status_code=400, message="请输入正确的手机号")
    if not sms_service.verify_code(phone, body.code, purpose="bind"):
        raise AppException(status_code=400, message="验证码错误或已过期")

    profile = wechat_mp_service.fetch_user_info(openid)
    unionid = unionid or profile.get("unionid")
    raw_sub = profile.get("subscribe")

    # Three outcomes: ① this WeChat already has an account ② this phone number already has an account (old email user) ③ brand new
    user = user_service.get_user_by_wechat(openid, unionid)
    if user:
        if user.phone and user.phone != phone:
            raise AppException(status_code=409, message="该微信已绑定其他手机号")
        user = user_service.attach_wechat(
            user.id, openid, unionid, profile.get("nickname"), profile.get("avatar"), phone,
        )
    else:
        existing = user_service.get_user_by_phone(phone)
        if existing:
            if existing.wechat_openid and existing.wechat_openid != openid:
                raise AppException(status_code=409, message="该手机号已绑定其他微信")
            user = user_service.attach_wechat(
                existing.id, openid, unionid, profile.get("nickname"), profile.get("avatar"), phone,
            )
        else:
            user = user_service.create_wechat_user(
                phone, openid, unionid, profile.get("nickname"), profile.get("avatar"),
            )

    if not user:
        raise AppException(status_code=500, message="账号创建失败,请重试")

    # attach_wechat / create_wechat_user hard-code wechat_subscribed to True — correct for the QR channel
    # (an event being pushed means they followed), but non-followers in the H5 channel can also reach this step.
    # Without correcting it the DB is all "followed", and later filtering on it to push reports would hit people who never followed.
    if raw_sub is not None and not bool(raw_sub):
        user_service.set_wechat_subscribed(openid, False)

    if scene:
        db = SessionLocal()
        try:
            row = db.query(WechatLoginSessionORM).filter(
                WechatLoginSessionORM.scene == scene).first()
            if row:
                row.user_id = user.id
                row.status = "consumed"
                row.consumed_at = datetime.utcnow()
                db.commit()
        finally:
            db.close()

    return {"access_token": _issue_access_token(user.email), "token_type": "bearer"}


# ── Official account server callback ────────────────────────────────────────────────────────

@router.get("/wechat/callback")
async def wechat_callback_verify(request: Request):
    """URL verification when "submit" is clicked in the official account platform: echo echostr back as-is."""
    q = request.query_params
    if wechat_mp_service.check_signature(
        q.get("signature", ""), q.get("timestamp", ""), q.get("nonce", "")
    ):
        return Response(content=q.get("echostr", ""), media_type="text/plain")
    raise AppException(status_code=403, message="signature check failed")


@router.post("/wechat/callback")
async def wechat_callback(request: Request):
    """Receive subscribe / SCAN / unsubscribe events.

    WeChat requires a response within 5 seconds and re-pushes 3 times on timeout — so only persist here, no slow work.
    """
    q = request.query_params
    if not wechat_mp_service.check_signature(
        q.get("signature", ""), q.get("timestamp", ""), q.get("nonce", "")
    ):
        raise AppException(status_code=403, message="signature check failed")

    try:
        msg = wechat_mp_service.parse_event(await request.body())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to parse official account push: %s", exc)
        return Response(content="success", media_type="text/plain")

    event = (msg.get("Event") or "").lower()
    openid = msg.get("FromUserName") or ""
    to_user = msg.get("ToUserName") or ""
    event_key = msg.get("EventKey") or ""

    if event == "unsubscribe" and openid:
        user_service.set_wechat_subscribed(openid, False)
        return Response(content="success", media_type="text/plain")

    if event not in ("subscribe", "scan") or not openid:
        return Response(content="success", media_type="text/plain")

    # The subscribe event's EventKey has a qrscene_ prefix; the SCAN event does not
    scene = event_key[len("qrscene_"):] if event_key.startswith("qrscene_") else event_key
    # Two scenarios, different replies:
    #  · Website PC QR login: scene matches a login session → persist + reply "return to the web page to finish login".
    #  · Acquisition follow (menu/card code/search, no scene or no matching session) → reply with an invitation containing the landing page link.
    # Previously not distinguished; everything got the login copy, so people following at field promotions saw "please return to the web page" and were confused.
    is_login = bool(scene) and _confirm_scene(scene, openid)
    if is_login:
        return _ack(to_user, openid, settings.WECHAT_LOGIN_WELCOME_TEXT)
    return _ack(to_user, openid, _subscribe_invite_text())


def _subscribe_invite_text() -> str:
    """Welcome message for acquisition follows. {link} is replaced with the landing page (home), rendered by WeChat as a clickable link."""
    tpl = settings.WECHAT_SUBSCRIBE_WELCOME_TEXT
    if not tpl:
        return ""
    base = (settings.WECHAT_H5_BASE_URL or settings.FRONTEND_URL or "").strip().rstrip("/")
    link = f"{base}{settings.WECHAT_PROMO_PATH}?from=subscribe" if base else ""
    return tpl.replace("{link}", link)


def _ack(to_user: str, openid: str, text: str) -> Response:
    """Reply. When text is empty, or a plaintext reply would be rejected by WeChat in secure mode, just reply with an empty success ack."""
    if text and not settings.WECHAT_MP_AES_KEY:
        return Response(
            content=wechat_mp_service.text_reply(openid, to_user, text),
            media_type="application/xml",
        )
    return Response(content="success", media_type="text/plain")


def _confirm_scene(scene: str, openid: str) -> bool:
    """Apply the scan event to the scene. Phone number bound → confirmed; otherwise → need_phone.

    Returns whether a login session was matched (i.e. this is a website PC QR login). Matched = True (whatever state it lands in,
    it belongs to the login flow, and the caller replies "return to the web page"); scene not found = False (treated as an acquisition follow).
    """
    now = datetime.utcnow()
    db = SessionLocal()
    try:
        row = db.query(WechatLoginSessionORM).filter(
            WechatLoginSessionORM.scene == scene).first()
        if not row:
            logger.info("Received scan event with unknown scene (treated as acquisition follow): %s", scene)
            return False
        if row.status not in ("pending",):
            return True
        if row.expires_at <= now:
            row.status = "expired"
            db.commit()
            return True

        row.openid = openid
        row.subscribed = True
        row.scanned_at = now

        user = user_service.get_user_by_wechat(openid)
        unionid = user.wechat_unionid if user else None
        if user and user.phone:
            row.user_id = user.id
            row.unionid = unionid
            row.status = "confirmed"
        else:
            row.unionid = unionid
            row.bind_token = _issue_bind_token(scene, openid, unionid)
            row.status = "need_phone"
        db.commit()
        return True
    finally:
        db.close()
