"""WeChat Pay Native (QR code payment) integration for domestic / Chinese-locale subscriptions.

Flow:
    1. Frontend calls POST /api/payment/wechat/create with a membership slug.
       Backend creates a WeChat Native Pay prepay order via the V3 API, persists
       a PaymentSessionORM row in 'pending' state, and returns the code_url
       (QR code content string).
    2. Frontend renders the code_url as a QR code. User scans with WeChat.
    3. WeChat fires a notification at our POST /api/payment/wechat/notify
       endpoint. We verify the V3 signature, flip the row to 'paid', and call
       membership_service.upgrade_membership().
    4. Safety net: the frontend polls GET /api/payment/wechat/status/{id},
       which can also query the WeChat API and fulfill if the webhook hasn't
       arrived yet.
"""

import base64
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import quote

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.padding import PKCS1v15
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from geo.database import SessionLocal, settings
from geo.models.balance import KIND_RECHARGE
from geo.models.membership import MembershipORM
from geo.models.payment import PaymentSessionORM
from geo.models.user import UserORM
from geo.services.membership_service import membership_service
from geo.services.price_override import get_cn_price_cny, get_test_price_override
from geo.utils.error_handler import AppException

logger = logging.getLogger("geo.wechat_pay")

WECHAT_API_BASE = "https://api.mch.weixin.qq.com"

# The out_trade_no prefix distinguishes order scenarios. There's no separate scene column; the prefix separates the two kinds:
# when Native reuses a pending order it must not pick up an H5 order (H5 stores h5_url, which is wrong to render as a QR code).
# 'wechat_' and 'wechath5_' aren't prefixes of each other, so the check is unambiguous.
NATIVE_PREFIX = "wechat_"
TOPUP_MIN_CENTS = 1000        # minimum top-up 10 CNY
TOPUP_MAX_CENTS = 5_000_00    # max 5000 CNY per transaction; more goes via corporate transfer
H5_PREFIX = "wechath5_"

# Error code WeChat returns when an H5 order is blocked by merchant product permissions. A hit means "H5 Pay" isn't enabled under
# merchant platform > product center -- not a problem with how we built the request; the caller should fall back to QR.
H5_NOT_ENABLED_CODES = {"NO_AUTH"}


WECHAT_MP_API_BASE = "https://api.weixin.qq.com"
WECHAT_OAUTH_AUTHORIZE = "https://open.weixin.qq.com/connect/oauth2/authorize"

JSAPI_PREFIX = "wechatjs_"


class JsapiNotConfigured(Exception):
    """Official account AppSecret not configured; can't exchange an openid. The caller falls back to QR."""


class H5NotEnabled(Exception):
    """Merchant hasn't enabled "H5 Pay". Not an error response; a signal for the caller to fall back to QR."""

    def __init__(self, code: str = "NO_AUTH"):
        self.code = code
        super().__init__(f"WeChat H5 pay not enabled for this merchant ({code})")


def _load_private_key():
    """Load the merchant RSA private key from the PEM file."""
    path = settings.WECHAT_PAY_PRIVATE_KEY_PATH
    if not path:
        return None
    with open(path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def _ensure_configured() -> None:
    if not settings.WECHAT_PAY_ENABLED:
        raise AppException(status_code=503, message="WeChat Pay is not enabled")
    required = [
        settings.WECHAT_PAY_APP_ID,
        settings.WECHAT_PAY_MCH_ID,
        settings.WECHAT_PAY_API_KEY_V3,
        settings.WECHAT_PAY_CERT_SERIAL,
        settings.WECHAT_PAY_PRIVATE_KEY_PATH,
    ]
    if not all(required):
        raise AppException(
            status_code=503,
            message="WeChat Pay is not fully configured (missing credentials)",
        )


def _build_authorization(method: str, url_path: str, body: str) -> str:
    """Build the Authorization header value for WeChat Pay V3 API requests.

    Signature format (RFC):
        HTTP Method\n
        URL path (no host)\n
        Timestamp\n
        Nonce\n
        Body\n
    Signed with RSA-SHA256 using the merchant private key.
    """
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    message = f"{method}\n{url_path}\n{timestamp}\n{nonce}\n{body}\n"

    private_key = _load_private_key()
    signature = private_key.sign(message.encode("utf-8"), PKCS1v15(), hashes.SHA256())
    signature_b64 = base64.b64encode(signature).decode("utf-8")

    return (
        f'WECHATPAY2-SHA256-RSA2048 '
        f'mchid="{settings.WECHAT_PAY_MCH_ID}",'
        f'nonce_str="{nonce}",'
        f'timestamp="{timestamp}",'
        f'serial_no="{settings.WECHAT_PAY_CERT_SERIAL}",'
        f'signature="{signature_b64}"'
    )


def _decrypt_resource(resource: Dict[str, Any]) -> Dict[str, Any]:
    """Decrypt the notification resource using AEAD_AES_256_GCM.

    WeChat V3 notifications encrypt the actual payment result inside
    resource.ciphertext, with resource.nonce and resource.associated_data.
    """
    api_key = settings.WECHAT_PAY_API_KEY_V3.encode("utf-8")
    nonce = resource["nonce"].encode("utf-8")
    associated_data = resource.get("associated_data", "").encode("utf-8")
    ciphertext = base64.b64decode(resource["ciphertext"])

    aesgcm = AESGCM(api_key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, associated_data)
    return json.loads(plaintext.decode("utf-8"))


class WechatPayService:
    def _resolve_amount_fen(self, membership: MembershipORM, user: UserORM) -> int:
        """Tier price -> WeChat amount in cents. Shared by three channels (QR / H5 / JSAPI).

        The price lookup order must exactly match the display price of /api/memberships (api/membership.py's
        _localize_price uses the same get_cn_price_cny + the same exchange rate),
        otherwise you get "page says ¥39.9, QR wants ¥71.93":
          1. CN site's independent CNY pricing (authoritative)
          2. the tier itself is priced in CNY
          3. fallback: USD price x settings.USD_TO_CNY_RATE
        """
        cn_price = get_cn_price_cny(membership.slug) if settings.is_cn else None
        currency = (membership.currency or "usd").lower()
        if cn_price is not None:
            amount_fen = int(round(cn_price * 100))
        elif currency == "cny":
            amount_fen = int(round(float(membership.price) * 100))
        else:
            amount_fen = int(
                round(float(membership.price) * settings.USD_TO_CNY_RATE * 100)
            )

        # WeChat always settles in CNY, so test account discounts look up the CNY tier.
        override = get_test_price_override(user.email, "cny")
        if override is not None:
            logger.warning(
                "WeChat test-price override: user=%s tier=%s amount %d fen -> %d fen",
                user.email, membership.slug, amount_fen, override,
            )
            amount_fen = override
        return amount_fen

    def _load_payable_membership(self, db, membership_slug: str) -> MembershipORM:
        """Get a tier eligible for self-service payment; raise a business exception directly if not eligible."""
        membership = (
            db.query(MembershipORM)
            .filter(MembershipORM.slug == membership_slug)
            .first()
        )
        if not membership:
            raise AppException(
                status_code=404, message=f"Membership '{membership_slug}' not found"
            )
        if membership.tier_type != "saas":
            raise AppException(
                status_code=400,
                message=f"{membership.name} is a managed service -- contact sales",
            )
        if membership.price <= 0:
            raise AppException(
                status_code=400, message="Free tier does not require payment"
            )
        return membership

    # ── JSAPI (WeChat in-app browser) ───────────────────────────────────
    #
    # Three steps: (1) send the user to official account web authorization (snsapi_base silent, invisible to the user)
    #              (2) WeChat comes back with a code; exchange the code for an openid
    #              (3) create a JSAPI order with the openid and sign the invocation params for the frontend WeixinJSBridge
    #
    # Advantages over H5: permission already enabled, and it's WeChat's native payment dialog without bouncing around.
    # Limitation: only works inside the WeChat in-app browser.

    def jsapi_configured(self) -> bool:
        return bool(settings.WECHAT_MP_APP_SECRET and settings.WECHAT_PAY_APP_ID)

    def build_oauth_url(self, redirect_uri: str, state: str = "") -> str:
        """Build the official account web authorization URL.

        scope uses snsapi_base -- only the openid, no authorization page popup, completely invisible to users.
        WeChat requires redirect_uri to be urlencoded, and its domain must be in the official account backend's
        "web authorization domain" allowlist, otherwise it reports a redirect_uri parameter error.
        """
        params = (
            f"appid={settings.WECHAT_PAY_APP_ID}"
            f"&redirect_uri={quote(redirect_uri, safe='')}"
            f"&response_type=code"
            f"&scope=snsapi_base"
            f"&state={quote(state, safe='')}"
        )
        return f"{WECHAT_OAUTH_AUTHORIZE}?{params}#wechat_redirect"

    def exchange_code_for_openid(self, code: str) -> str:
        """Exchange the web authorization code for an openid. The code is valid for five minutes and single-use."""
        if not self.jsapi_configured():
            raise JsapiNotConfigured()

        with httpx.Client(timeout=15) as client:
            resp = client.get(
                f"{WECHAT_MP_API_BASE}/sns/oauth2/access_token",
                params={
                    "appid": settings.WECHAT_PAY_APP_ID,
                    "secret": settings.WECHAT_MP_APP_SECRET,
                    "code": code,
                    "grant_type": "authorization_code",
                },
            )
        data = resp.json() if resp.status_code == 200 else {}
        openid = data.get("openid")
        if not openid:
            logger.warning("official account code -> openid exchange failed: %s", data)
            raise AppException(
                status_code=400,
                message=f"WeChat authorization failed: {data.get('errmsg', 'no openid')}",
            )
        return openid

    def _build_jsapi_pay_signature(self, prepay_id: str) -> Dict[str, Any]:
        """Sign the set of params the frontend WeixinJSBridge needs to invoke payment.

        The string to sign is a fixed four lines (each followed by \\n):
            appId \\n timeStamp \\n nonceStr \\n package \\n
        Signed with the merchant private key using RSA-SHA256; signType is fixed 'RSA'.
        """
        timestamp = str(int(time.time()))
        nonce = uuid.uuid4().hex
        package = f"prepay_id={prepay_id}"
        message = f"{settings.WECHAT_PAY_APP_ID}\n{timestamp}\n{nonce}\n{package}\n"

        private_key = _load_private_key()
        signature = private_key.sign(message.encode("utf-8"), PKCS1v15(), hashes.SHA256())

        return {
            "appId": settings.WECHAT_PAY_APP_ID,
            "timeStamp": timestamp,
            "nonceStr": nonce,
            "package": package,
            "signType": "RSA",
            "paySign": base64.b64encode(signature).decode("utf-8"),
        }

    def create_jsapi_order(
        self, user: UserORM, membership_slug: str, openid: str
    ) -> Dict[str, Any]:
        """Create a JSAPI order; returns invocation params the frontend can feed directly to WeixinJSBridge."""
        _ensure_configured()

        db = SessionLocal()
        try:
            membership = self._load_payable_membership(db, membership_slug)
            amount_fen = self._resolve_amount_fen(membership, user)
            out_trade_no = f"{JSAPI_PREFIX}{uuid.uuid4().hex[:16]}"

            url_path = "/v3/pay/transactions/jsapi"
            request_body = {
                "appid": settings.WECHAT_PAY_APP_ID,
                "mchid": settings.WECHAT_PAY_MCH_ID,
                "description": f"GEO Readiness Checker - {membership.name}"[:127],
                "out_trade_no": out_trade_no,
                "notify_url": settings.WECHAT_PAY_NOTIFY_URL,
                "amount": {"total": amount_fen, "currency": "CNY"},
                "payer": {"openid": openid},
            }
            body_str = json.dumps(request_body, ensure_ascii=False)
            authorization = _build_authorization("POST", url_path, body_str)

            with httpx.Client(timeout=15) as client:
                resp = client.post(
                    f"{WECHAT_API_BASE}{url_path}",
                    content=body_str,
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "Authorization": authorization,
                    },
                )

            if resp.status_code != 200:
                logger.error(
                    "WeChat JSAPI prepay failed: status=%d body=%s",
                    resp.status_code, resp.text,
                )
                raise AppException(
                    status_code=502,
                    message=f"WeChat Pay API error: {resp.status_code}",
                )

            prepay_id = (resp.json() or {}).get("prepay_id")
            if not prepay_id:
                raise AppException(
                    status_code=502, message="WeChat Pay did not return a prepay_id"
                )

            row = PaymentSessionORM(
                stripe_session_id=out_trade_no,
                user_id=user.id,
                membership_id=membership.id,
                amount_cents=amount_fen,
                currency="cny",
                status="pending",
                provider="wechat",
                created_at=datetime.utcnow(),
            )
            db.add(row)
            db.commit()
            db.refresh(row)

            return {
                "payment_id": row.id,
                "amount_cny": amount_fen / 100.0,
                "out_trade_no": out_trade_no,
                "pay_params": self._build_jsapi_pay_signature(prepay_id),
            }
        finally:
            db.close()

    def create_h5_order(
        self,
        user: UserORM,
        membership_slug: str,
        client_ip: str,
        redirect_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a WeChat H5 (MWEB) payment order, for mobile browsers to launch the WeChat app directly.

        Returns {"payment_id", "h5_url", "amount_cny", "out_trade_no"}; the frontend just sets
        location.href to h5_url. h5_url is valid for only 5 minutes and can only be opened in
        mobile browsers (the WeChat in-app browser is blocked by WeChat -- that scenario should use QR/JSAPI).

        Raises H5NotEnabled when the merchant hasn't enabled "H5 Pay"; the caller falls back to QR.
        """
        _ensure_configured()

        db = SessionLocal()
        try:
            membership = self._load_payable_membership(db, membership_slug)
            amount_fen = self._resolve_amount_fen(membership, user)
            out_trade_no = f"{H5_PREFIX}{uuid.uuid4().hex[:16]}"

            url_path = "/v3/pay/transactions/h5"
            request_body = {
                "appid": settings.WECHAT_PAY_APP_ID,
                "mchid": settings.WECHAT_PAY_MCH_ID,
                "description": f"GEO Readiness Checker - {membership.name}"[:127],
                "out_trade_no": out_trade_no,
                "notify_url": settings.WECHAT_PAY_NOTIFY_URL,
                "amount": {"total": amount_fen, "currency": "CNY"},
                "scene_info": {
                    # WeChat requires the user's real IP; give a placeholder when unavailable so the order doesn't fail
                    "payer_client_ip": client_ip or "0.0.0.0",
                    "h5_info": {
                        "type": "Wap",
                        "app_name": "Vigilath",
                        "app_url": settings.FRONTEND_URL,
                    },
                },
            }
            body_str = json.dumps(request_body, ensure_ascii=False)
            authorization = _build_authorization("POST", url_path, body_str)

            with httpx.Client(timeout=15) as client:
                resp = client.post(
                    f"{WECHAT_API_BASE}{url_path}",
                    content=body_str,
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "Authorization": authorization,
                    },
                )

            if resp.status_code != 200:
                code = ""
                try:
                    code = (resp.json() or {}).get("code", "")
                except ValueError:
                    pass
                if code in H5_NOT_ENABLED_CODES:
                    logger.warning(
                        "WeChat H5 order rejected (product permission not enabled): status=%d code=%s",
                        resp.status_code, code,
                    )
                    raise H5NotEnabled(code)
                logger.error(
                    "WeChat H5 prepay failed: status=%d body=%s",
                    resp.status_code, resp.text,
                )
                raise AppException(
                    status_code=502,
                    message=f"WeChat Pay API error: {resp.status_code}",
                )

            h5_url = (resp.json() or {}).get("h5_url")
            if not h5_url:
                raise AppException(
                    status_code=502, message="WeChat Pay did not return an h5_url"
                )

            row = PaymentSessionORM(
                stripe_session_id=out_trade_no,
                user_id=user.id,
                membership_id=membership.id,
                amount_cents=amount_fen,
                currency="cny",
                status="pending",
                provider="wechat",
                checkout_url=h5_url,
                created_at=datetime.utcnow(),
            )
            db.add(row)
            db.commit()
            db.refresh(row)

            # Where WeChat brings the user back after paying/cancelling (optional). Include payment_id,
            # because WeChat also uses redirect_url on "cancel payment" -- the landing page must check the real
            # status before deciding to show success or incomplete; it can't report success on arrival.
            # urlencode the whole string, otherwise its own query would truncate h5_url's parameters.
            if redirect_url:
                sep = "&" if "?" in redirect_url else "?"
                back = f"{redirect_url}{sep}payment_id={row.id}"
                h5_url = f"{h5_url}&redirect_url={quote(back, safe='')}"

            return {
                "payment_id": row.id,
                "h5_url": h5_url,
                "amount_cny": amount_fen / 100.0,
                "out_trade_no": out_trade_no,
            }
        finally:
            db.close()


    def create_topup_native_order(self, user: UserORM, amount_cents: int) -> dict:
        """Top up the compute-points wallet (QR). Same native path as buying a plan, with only two differences:
        the amount is given by the caller, and purpose='topup' -- fulfillment on callback branches on it and doesn't activate any plan.

        Amount units align: the wallet records CNY cents, WeChat total is also cents, 1:1, no conversion.
        """
        # Same validation as the plan purchase path (a module-level function, not a method) --
        # I originally wrote self.is_configured(), a method that doesn't exist,
        # and unit tests mocked out the whole order function, so it only blew up when it really ran in production.
        _ensure_configured()
        if amount_cents < TOPUP_MIN_CENTS or amount_cents > TOPUP_MAX_CENTS:
            raise AppException(
                status_code=400,
                message=f"单笔充值范围 ¥{TOPUP_MIN_CENTS / 100:.0f}–¥{TOPUP_MAX_CENTS / 100:.0f}",
            )

        db = SessionLocal()
        try:
            out_trade_no = f"{NATIVE_PREFIX}{uuid.uuid4().hex[:16]}"
            url_path = "/v3/pay/transactions/native"
            request_body = {
                "appid": settings.WECHAT_PAY_APP_ID,
                "mchid": settings.WECHAT_PAY_MCH_ID,
                "description": f"Vigilath 算力点充值 ¥{amount_cents / 100:.2f}"[:127],
                "out_trade_no": out_trade_no,
                "notify_url": settings.WECHAT_PAY_NOTIFY_URL,
                "amount": {"total": int(amount_cents), "currency": "CNY"},
            }
            body_str = json.dumps(request_body, ensure_ascii=False)
            authorization = _build_authorization("POST", url_path, body_str)
            with httpx.Client(timeout=15) as client:
                resp = client.post(
                    f"{WECHAT_API_BASE}{url_path}",
                    content=body_str,
                    headers={"Content-Type": "application/json", "Accept": "application/json",
                             "Authorization": authorization},
                )
            if resp.status_code != 200:
                logger.error("WeChat topup prepay failed: status=%d body=%s", resp.status_code, resp.text)
                raise AppException(status_code=502, message=f"WeChat Pay API error: {resp.status_code}")
            code_url = (resp.json() or {}).get("code_url")
            if not code_url:
                raise AppException(status_code=502, message="WeChat Pay did not return a code_url")

            row = PaymentSessionORM(
                stripe_session_id=out_trade_no,
                user_id=user.id,
                membership_id=None,          # top-up orders have no plan
                purpose="topup",
                amount_cents=int(amount_cents),
                currency="cny",
                status="pending",
                provider="wechat",
                checkout_url=code_url,
                created_at=datetime.utcnow(),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return {"payment_id": row.id, "code_url": code_url,
                    "amount_cny": amount_cents / 100.0, "out_trade_no": out_trade_no,
                    "purpose": "topup"}
        finally:
            db.close()

    def create_native_order(
        self,
        user: UserORM,
        membership_slug: str,
    ) -> Dict[str, Any]:
        """Create a WeChat Native Pay prepay order.

        Returns {"payment_id": ..., "code_url": ..., "amount_cny": ..., "out_trade_no": ...}.
        The frontend renders code_url as a QR code for the user to scan.
        """
        _ensure_configured()

        db = SessionLocal()
        try:
            membership = self._load_payable_membership(db, membership_slug)

            # Reuse a recent pending session (< 90 min, within WeChat QR validity).
            # Only Native orders: H5 orders store h5_url, which is wrong to render as a QR code.
            existing = (
                db.query(PaymentSessionORM)
                .filter(
                    PaymentSessionORM.user_id == user.id,
                    PaymentSessionORM.membership_id == membership.id,
                    PaymentSessionORM.provider == "wechat",
                    PaymentSessionORM.status == "pending",
                    PaymentSessionORM.stripe_session_id.startswith(
                        NATIVE_PREFIX, autoescape=True
                    ),
                )
                .order_by(PaymentSessionORM.created_at.desc())
                .first()
            )
            if existing:
                age_minutes = (datetime.utcnow() - existing.created_at).total_seconds() / 60
                if age_minutes < 90 and existing.checkout_url:
                    return {
                        "payment_id": existing.id,
                        "code_url": existing.checkout_url,
                        "amount_cny": existing.amount_cents / 100.0,
                        "out_trade_no": existing.stripe_session_id,
                    }
                existing.status = "expired"
                db.commit()

            amount_fen = self._resolve_amount_fen(membership, user)
            out_trade_no = f"{NATIVE_PREFIX}{uuid.uuid4().hex[:16]}"

            # Call WeChat V3 API: POST /v3/pay/transactions/native
            url_path = "/v3/pay/transactions/native"
            request_body = {
                "appid": settings.WECHAT_PAY_APP_ID,
                "mchid": settings.WECHAT_PAY_MCH_ID,
                "description": f"GEO Readiness Checker - {membership.name}"[:127],
                "out_trade_no": out_trade_no,
                "notify_url": settings.WECHAT_PAY_NOTIFY_URL,
                "amount": {
                    "total": amount_fen,
                    "currency": "CNY",
                },
            }
            body_str = json.dumps(request_body, ensure_ascii=False)
            authorization = _build_authorization("POST", url_path, body_str)

            with httpx.Client(timeout=15) as client:
                resp = client.post(
                    f"{WECHAT_API_BASE}{url_path}",
                    content=body_str,
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "Authorization": authorization,
                    },
                )

            if resp.status_code != 200:
                logger.error(
                    "WeChat prepay failed: status=%d body=%s",
                    resp.status_code,
                    resp.text,
                )
                raise AppException(
                    status_code=502,
                    message=f"WeChat Pay API error: {resp.status_code}",
                )

            result = resp.json()
            code_url = result.get("code_url")
            if not code_url:
                raise AppException(
                    status_code=502,
                    message="WeChat Pay did not return a code_url",
                )

            # Persist payment session
            row = PaymentSessionORM(
                stripe_session_id=out_trade_no,
                user_id=user.id,
                membership_id=membership.id,
                amount_cents=amount_fen,
                currency="cny",
                status="pending",
                provider="wechat",
                checkout_url=code_url,
                created_at=datetime.utcnow(),
            )
            db.add(row)
            db.commit()
            db.refresh(row)

            return {
                "payment_id": row.id,
                "code_url": code_url,
                "amount_cny": amount_fen / 100.0,
                "out_trade_no": out_trade_no,
            }
        finally:
            db.close()

    def handle_notify(
        self, body: bytes, headers: Dict[str, str]
    ) -> Dict[str, Any]:
        """Process a WeChat Pay V3 notification.

        Verifies the signature (via decryption), fulfills the order.
        Returns a dict suitable for the 200 response body WeChat expects.
        """
        _ensure_configured()

        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            raise AppException(status_code=400, message="Invalid notification payload")

        event_type = payload.get("event_type", "")
        resource = payload.get("resource")
        if not resource:
            raise AppException(status_code=400, message="Missing resource in notification")

        # Decrypt the resource to get the actual payment result
        try:
            result = _decrypt_resource(resource)
        except Exception as e:
            logger.error("WeChat notification decryption failed: %s", e)
            raise AppException(
                status_code=400,
                message="Failed to decrypt notification resource",
            )

        trade_state = result.get("trade_state")
        out_trade_no = result.get("out_trade_no")

        if trade_state == "SUCCESS" and out_trade_no:
            self._fulfill_if_needed(out_trade_no)

        return {"code": "SUCCESS", "message": "OK"}

    def query_order(self, out_trade_no: str) -> Dict[str, Any]:
        """Query order status from WeChat API. Used as a safety-net."""
        _ensure_configured()

        url_path = f"/v3/pay/transactions/out-trade-no/{out_trade_no}?mchid={settings.WECHAT_PAY_MCH_ID}"
        authorization = _build_authorization("GET", url_path, "")

        with httpx.Client(timeout=15) as client:
            resp = client.get(
                f"{WECHAT_API_BASE}{url_path}",
                headers={
                    "Accept": "application/json",
                    "Authorization": authorization,
                },
            )

        if resp.status_code != 200:
            return {"trade_state": "UNKNOWN"}

        return resp.json()

    def refund(self, out_trade_no: str, amount_cents: int, reason: str = "") -> Dict[str, Any]:
        """Refund via the original route by merchant order number. **If we took the money and can't deliver, we must refund** -- the bottom line of the paid flow.

        Idempotency via out_refund_no: repeated refunds on the same order are treated by WeChat as the same refund, never refunded twice.
        Returns {"ok": bool, "status": str, "refund_id": str, "error": str}.
        **Never raises** -- callers are usually already in the "business failed" branch; another exception would only
        bury the error, leaving users with neither the goods nor any idea what happened to their money.
        """
        _ensure_configured()

        url_path = "/v3/refund/domestic/refunds"
        body = {
            "out_trade_no": out_trade_no,
            "out_refund_no": f"rf_{out_trade_no}"[:64],   # one refund number per order, naturally idempotent
            "reason": (reason or "服务异常无法提供内容")[:80],
            "amount": {
                "refund": int(amount_cents),
                "total": int(amount_cents),
                "currency": "CNY",
            },
        }
        body_str = json.dumps(body, ensure_ascii=False)
        try:
            authorization = _build_authorization("POST", url_path, body_str)
            with httpx.Client(timeout=20) as client:
                resp = client.post(
                    f"{WECHAT_API_BASE}{url_path}",
                    content=body_str,
                    headers={"Content-Type": "application/json", "Accept": "application/json",
                             "Authorization": authorization},
                )
        except Exception as e:  # noqa: BLE001
            logger.error("WeChat refund request failed: out_trade_no=%s err=%s", out_trade_no, e)
            return {"ok": False, "status": "REQUEST_FAILED", "refund_id": "", "error": str(e)[:200]}

        if resp.status_code not in (200, 202):
            logger.error("WeChat refund failed: out_trade_no=%s status=%d body=%s",
                         out_trade_no, resp.status_code, resp.text[:300])
            return {"ok": False, "status": f"HTTP_{resp.status_code}", "refund_id": "",
                    "error": resp.text[:200]}

        data = resp.json() or {}
        logger.info("WeChat refund accepted: out_trade_no=%s refund_id=%s status=%s",
                    out_trade_no, data.get("refund_id"), data.get("status"))
        return {"ok": True, "status": str(data.get("status") or "PROCESSING"),
                "refund_id": str(data.get("refund_id") or ""), "error": ""}


    def _fulfill_if_needed(self, out_trade_no: str) -> bool:
        """Idempotently mark the order as paid and upgrade membership.

        Returns True if this call performed the fulfillment.
        """
        db = SessionLocal()
        try:
            row = (
                db.query(PaymentSessionORM)
                .filter(PaymentSessionORM.stripe_session_id == out_trade_no)
                .first()
            )
            if row is None:
                logger.warning("WeChat fulfill: no row for out_trade_no=%s", out_trade_no)
                return False
            if row.status == "paid":
                return False

            row.status = "paid"
            row.completed_at = datetime.utcnow()
            db.commit()

            # Branch by purpose. Top-up orders have no membership_id; reaching upgrade_membership would blow up,
            # and the money must go into the wallet rather than activate any plan.
            if (getattr(row, "purpose", "") or "membership") == "topup":
                from geo.services import balance_service

                res = balance_service.credit(
                    # ★ kind must be KIND_RECHARGE. The recharged_cents accumulation in credit()
                    #   only recognizes "recharge"/"grant"; with another string the money goes in but "total recharged" stays 0
                    db, user_id=row.user_id, kind=KIND_RECHARGE,
                    amount_cents=int(row.amount_cents or 0),
                    idem_key=f"wxpay:{out_trade_no}",      # WeChat callbacks get re-pushed; the idempotency key pins the order number
                    ref_type="payment_session", ref_id=str(row.id),
                    note={"provider": "wechat", "out_trade_no": out_trade_no},
                )
                db.commit()
                logger.info("WeChat topup fulfilled: user_id=%d amount_cents=%d status=%s",
                            row.user_id, row.amount_cents or 0, res.status)
                return True

            # Buying membership = get the tier + an equal amount of credits. Activation and crediting both live in membership_billing,
            # shared by the three gateways (WeChat / Stripe / MoltsPay); don't write another copy here.
            from geo.services.membership_billing import fulfill_paid_membership

            fulfill_paid_membership(db, row, provider="wechat", order_ref=out_trade_no)
            return True
        finally:
            db.close()


wechat_pay_service = WechatPayService()
