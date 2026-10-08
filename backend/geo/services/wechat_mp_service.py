"""WeChat official account (service account) API wrapper -- for QR login.

Flow:
    1. POST /cgi-bin/qrcode/create to create a "parameterized temporary QR code", get a ticket
    2. Frontend renders https://mp.weixin.qq.com/cgi-bin/showqrcode?ticket=...
    3. User scans:
       - not following -> WeChat prompts to follow -> after following pushes a subscribe event, EventKey=qrscene_<scene>
       - already following -> pushes a SCAN event directly, EventKey=<scene>
       Both events carry FromUserName = openid, which is the identity we want.
    4. Events are pushed to POST /api/auth/wechat/callback (the URL in the official account backend's "server configuration")

There's also an H5 single-device channel (build_oauth_url / exchange_code_for_openid): the user taps into the landing page from
the official account menu or welcome message, and web authorization gives the openid directly, without a scene. At on-site promotion events
there's only one phone, so this is the path used.

Note: both the temporary QR code API and web authorization require a **verified service account**; an unverified subscription account calling the QR
API gets 48001, and doesn't have web authorization permission at all.
"""

import hashlib
import logging
import struct
import xml.etree.ElementTree as ET
from base64 import b64decode
from datetime import datetime, timedelta
from typing import Dict, Optional
from urllib.parse import quote

import httpx
from sqlalchemy.exc import IntegrityError

from geo.database import SessionLocal, settings
from geo.models.wechat_auth import WechatMpTokenORM

logger = logging.getLogger("geo.wechat_mp")

API_BASE = "https://api.weixin.qq.com"
SHOWQRCODE_URL = "https://mp.weixin.qq.com/cgi-bin/showqrcode"
OAUTH_AUTHORIZE_URL = "https://open.weixin.qq.com/connect/oauth2/authorize"

# Renew 5 minutes early so the token isn't judged expired by WeChat right at the edge of its validity
TOKEN_SAFETY_MARGIN = 300


class WechatMpNotConfigured(Exception):
    """Official account AppID / AppSecret not fully configured; QR login unavailable."""


def app_id() -> str:
    """AppID for login -- reuses the payment one only if not configured separately."""
    return settings.WECHAT_MP_APP_ID or settings.WECHAT_PAY_APP_ID


def app_secret() -> str:
    """AppSecret for login.

    Fetched separately from payment: the two can be different official accounts (that's the case on vigilath.cn production --
    payment uses wx4185..., login uses another account). Sharing one secret would use account A's secret to exchange
    account B's token; WeChat returns 40001, which looks like "config didn't take effect" and is hard to pin down.
    """
    return settings.WECHAT_LOGIN_APP_SECRET or settings.WECHAT_MP_APP_SECRET


def is_configured() -> bool:
    return bool(settings.WECHAT_LOGIN_ENABLED and app_id() and app_secret())


def _require_configured() -> None:
    if not is_configured():
        raise WechatMpNotConfigured(
            "微信扫码登录未配置(需要 WECHAT_LOGIN_ENABLED / AppID / AppSecret)"
        )


# ── access_token ──────────────────────────────────────────────────────────

def get_access_token(force_refresh: bool = False) -> str:
    """Get the official account's global access_token, cached in the DB and shared across workers."""
    _require_configured()
    appid = app_id()
    now = datetime.utcnow()

    db = SessionLocal()
    try:
        row = db.query(WechatMpTokenORM).filter(WechatMpTokenORM.app_id == appid).first()
        if row and not force_refresh and row.expires_at > now and row.access_token:
            return row.access_token

        with httpx.Client(timeout=15) as client:
            resp = client.get(
                f"{API_BASE}/cgi-bin/token",
                params={
                    "grant_type": "client_credential",
                    "appid": appid,
                    "secret": app_secret(),
                },
            )
        data = resp.json()
        token = data.get("access_token")
        if not token:
            # 40164 = IP not in the official account allowlist; the most common production failure, passed out as-is
            raise WechatMpNotConfigured(f"换取公众号 access_token 失败: {data}")

        expires_at = now + timedelta(seconds=int(data.get("expires_in", 7200)) - TOKEN_SAFETY_MARGIN)
        if row:
            row.access_token = token
            row.expires_at = expires_at
            row.updated_at = now
        else:
            db.add(WechatMpTokenORM(
                app_id=appid, access_token=token, expires_at=expires_at, updated_at=now,
            ))
        try:
            db.commit()
        except IntegrityError:
            # Two workers fetch the token for the first time simultaneously; whoever inserts first wins; the loser rolls back and can still use its own
            db.rollback()
        return token
    finally:
        db.close()


def _post_with_retry(path: str, payload: dict) -> dict:
    """POST with one token retry. 40001/42001 = token invalid; force a refresh and try again.

    ★ Must send UTF-8 with ensure_ascii=False, not httpx's json= -- it defaults to
      ensure_ascii=True, turning Chinese into \\uXXXX, and the menu API returns 40033
      (invalid charset ... if include \\uxxxx will create fail).
      The parameterized QR payload is all ASCII so we never hit it; it surfaced only because menu names are Chinese.
    """
    import json as _json
    for attempt in (0, 1):
        token = get_access_token(force_refresh=(attempt == 1))
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                f"{API_BASE}{path}",
                params={"access_token": token},
                content=_json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
        data = resp.json()
        if data.get("errcode") in (40001, 40014, 42001) and attempt == 0:
            continue
        return data
    return data


# ── Customer service messages ────────────────────────────────────────────────────────────────

# Customer service messages can only go to users who interacted with the official account within 48h; beyond the window WeChat returns this code
ERR_OUT_OF_48H = 45015


def send_customer_text(openid: str, text: str) -> dict:
    """Customer service message (text). Any text, may include links, no template needed; limited to followers who interacted within 48h.

    Returns WeChat's raw response; errcode 45015 = beyond the 48h window. Requires a verified service account.
    """
    _require_configured()
    return _post_with_retry("/cgi-bin/message/custom/send", {
        "touser": openid,
        "msgtype": "text",
        "text": {"content": text},
    })


# ── Parameterized temporary QR codes ────────────────────────────────────────────────────────

def create_login_qrcode(scene: str, expire_seconds: int) -> Dict[str, str]:
    """Create a temporary QR code; returns {ticket, qr_url, expire_seconds}."""
    _require_configured()
    data = _post_with_retry("/cgi-bin/qrcode/create", {
        "expire_seconds": expire_seconds,
        "action_name": "QR_STR_SCENE",
        "action_info": {"scene": {"scene_str": scene}},
    })
    ticket = data.get("ticket")
    if not ticket:
        raise WechatMpNotConfigured(f"生成登录二维码失败: {data}")
    return {
        "ticket": ticket,
        "qr_url": f"{SHOWQRCODE_URL}?ticket={quote(ticket, safe='')}",
        "expire_seconds": int(data.get("expire_seconds", expire_seconds)),
    }


# ── Web authorization (H5 single-device channel) ──────────────────────────────────────────────
#
# The QR login flow is a two-device model "PC shows code + phone scans": bind_token only comes from
# polling GET /wechat/qrcode/{scene}. At on-site promotion there's only one phone and no polling
# page, so that flow is broken. The H5 channel uses another leg: the user taps into the landing page from the official account menu / welcome message,
# and web authorization hands the openid straight to the landing page, no scene relay needed.


def build_oauth_url(redirect_uri: str, state: str = "", scope: str = "snsapi_base") -> str:
    """Build the official account web authorization URL.

    ★ Uses the **login account's** appid (`app_id()`), not the
      `WECHAT_PAY_APP_ID` in wechat_pay_service. In production these are two different official accounts (see the app_secret comment);
      exchanging account A's code at account B for an openid makes WeChat return 40163, which shows up as "blank page after authorization".

    scope defaults to snsapi_base -- only the openid, no authorization page popup, completely invisible to users.
    The **domain** of redirect_uri must be in the official account backend's "web authorization domain" allowlist,
    otherwise WeChat directly reports a redirect_uri parameter error and won't even redirect.
    """
    _require_configured()
    params = (
        f"appid={app_id()}"
        f"&redirect_uri={quote(redirect_uri, safe='')}"
        f"&response_type=code"
        f"&scope={scope}"
        f"&state={quote(state, safe='')}"
    )
    return f"{OAUTH_AUTHORIZE_URL}?{params}#wechat_redirect"


def exchange_code_for_openid(code: str) -> Dict[str, Optional[str]]:
    """Web authorization code -> {openid, unionid}. The code is valid for five minutes and **can only be used once**.

    Single use means the frontend can't leave the URL with the code in the address bar: a user refresh gives
    40163 (code been used), so after exchanging the openid the landing page must replaceState to erase the code.
    """
    _require_configured()
    with httpx.Client(timeout=15) as client:
        resp = client.get(
            f"{API_BASE}/sns/oauth2/access_token",
            params={
                "appid": app_id(),
                "secret": app_secret(),
                "code": code,
                "grant_type": "authorization_code",
            },
        )
    data = resp.json() if resp.status_code == 200 else {}
    openid = data.get("openid")
    if not openid:
        # 40163 = code already used (most likely the user refreshed the page with the code); 40029 = invalid code
        logger.warning("web authorization code -> openid exchange failed: %s", data)
        raise WechatMpNotConfigured(f"网页授权失败: {data.get('errmsg') or data}")
    return {"openid": openid, "unionid": data.get("unionid") or None}


def fetch_user_info(openid: str) -> Dict[str, Optional[str]]:
    """Fetch follower nickname / avatar. Failure isn't an error -- the nickname is only for display."""
    try:
        token = get_access_token()
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                f"{API_BASE}/cgi-bin/user/info",
                params={"access_token": token, "openid": openid, "lang": "zh_CN"},
            )
        data = resp.json()
        if data.get("errcode"):
            logger.warning("failed to fetch official account user info openid=%s: %s", openid, data)
            return {}
        return {
            "nickname": data.get("nickname") or None,
            "avatar": data.get("headimgurl") or None,
            "unionid": data.get("unionid") or None,
            # For non-followers this is 0, and nickname/avatar are always empty -- the H5 channel uses this bit to decide
            # whether to show a follow prompt on the landing page. When unavailable it's None (unknown), not treated as not following.
            "subscribe": data.get("subscribe"),
        }
    except Exception as exc:  # noqa: BLE001 — failing to fetch the profile shouldn't block login
        logger.warning("error fetching official account user info openid=%s: %s", openid, exc)
        return {}


# ── Custom menu ─────────────────────────────────────────────────────────────
#
# Why configure via the API instead of clicking in the official account backend: backend-configured menus have no versions and changes leave no trace,
# and this menu is the **only entry point** of the promotion flow -- which URL it points to decides whether the whole funnel works.
# Kept in scripts/setup_wechat_menu.py, the menu structure goes into git with the code.
#
# ★ All three of these APIs use the **global access_token**, so they're subject to the official account's "IP allowlist":
#   if the egress IP of the machine running the script isn't on the allowlist, they all return 40164. The two web authorization APIs
#   (build_oauth_url / exchange_code_for_openid) aren't subject to this limit; don't confuse them.


def create_menu(menu: dict) -> dict:
    """Create the custom menu, overwriting. WeChat has no "change one item" API; it's always a full replacement.

    When it takes effect: for existing followers refresh takes **up to 24 hours** (client-side cache);
    unfollow and refollow takes effect immediately. So not seeing it right away isn't failure -- verify with get_menu().
    """
    data = _post_with_retry("/cgi-bin/menu/create", menu)
    if data.get("errcode"):
        raise WechatMpNotConfigured(f"创建自定义菜单失败: {data}")
    return data


def get_menu() -> dict:
    """Read back the current menu, to verify what was actually written.

    If a menu was never configured WeChat returns 46003 (doesn't exist); that's not an error -- returned as-is for the caller to judge.
    """
    _require_configured()
    for attempt in (0, 1):
        token = get_access_token(force_refresh=(attempt == 1))
        with httpx.Client(timeout=15) as client:
            resp = client.get(f"{API_BASE}/cgi-bin/get_current_selfmenu_info",
                              params={"access_token": token})
        data = resp.json()
        if data.get("errcode") in (40001, 40014, 42001) and attempt == 0:
            continue
        return data
    return data


def delete_menu() -> dict:
    """Delete the custom menu, restoring the state without a bottom bar."""
    _require_configured()
    token = get_access_token()
    with httpx.Client(timeout=15) as client:
        resp = client.get(f"{API_BASE}/cgi-bin/menu/delete", params={"access_token": token})
    return resp.json()


# ── Server configuration verification / message parsing ────────────────────────────────────────────────

def check_signature(signature: str, timestamp: str, nonce: str) -> bool:
    """URL verification for the official account "server configuration" and source verification of each push."""
    token = settings.WECHAT_MP_TOKEN
    if not token:
        return False
    raw = "".join(sorted([token, timestamp or "", nonce or ""]))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest() == (signature or "")


def _aes_key() -> bytes:
    # EncodingAESKey is 43-char base64 (trailing '=' removed); add it back to get the 32-byte key
    return b64decode(settings.WECHAT_MP_AES_KEY + "=")


def decrypt_message(encrypt_b64: str) -> str:
    """Secure mode decryption. Returns plaintext XML."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = _aes_key()
    cipher = Cipher(algorithms.AES(key), modes.CBC(key[:16]))
    decryptor = cipher.decryptor()
    plain = decryptor.update(b64decode(encrypt_b64)) + decryptor.finalize()
    # Remove PKCS#7 padding
    plain = plain[: -plain[-1]]
    # Structure: 16 random bytes + 4-byte network-order length + XML + appid
    xml_len = struct.unpack(">I", plain[16:20])[0]
    return plain[20:20 + xml_len].decode("utf-8")


def parse_event(body: bytes) -> Dict[str, str]:
    """Parse official account pushes. Plaintext mode reads directly; secure mode decrypts <Encrypt> first."""
    root = ET.fromstring(body)
    encrypt = root.findtext("Encrypt")
    if encrypt and settings.WECHAT_MP_AES_KEY:
        root = ET.fromstring(decrypt_message(encrypt))
    return {child.tag: (child.text or "") for child in root}


def text_reply(to_user: str, from_user: str, content: str) -> str:
    """Reply a text message to the person who scanned (the official account requires a response within 5 seconds)."""
    ts = int(datetime.utcnow().timestamp())
    return (
        "<xml>"
        f"<ToUserName><![CDATA[{to_user}]]></ToUserName>"
        f"<FromUserName><![CDATA[{from_user}]]></FromUserName>"
        f"<CreateTime>{ts}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{content}]]></Content>"
        "</xml>"
    )
