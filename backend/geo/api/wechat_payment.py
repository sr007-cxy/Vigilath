"""WeChat Pay API routes.

Endpoints:
    POST /wechat/create       — Create a Native Pay prepay order (returns QR code_url)
    GET  /wechat/status/{id}  — Poll payment status (frontend polling)
    POST /wechat/notify       — WeChat async notification callback (no auth)
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from geo.api.auth import get_current_user
from geo.database import SessionLocal, settings
from geo.models.payment import PaymentSessionORM
from geo.models.user import UserORM
from geo.services.wechat_pay_service import (
    wechat_pay_service,
    H5NotEnabled,
    JsapiNotConfigured,
)
from geo.utils.error_handler import AppException

router = APIRouter()

logger = logging.getLogger("geo.wechat_pay")


class CreateWechatPayRequest(BaseModel):
    slug: str
    # 'native' = desktop QR scan (default); 'h5' = mobile browser, use h5_url to launch the WeChat app directly
    scene: Optional[str] = "native"
    # URL WeChat brings the user back to after H5 payment (optional)
    redirect_url: Optional[str] = None


def _client_ip(request: Request) -> str:
    """Get the user's real IP — WeChat H5 orders require payer_client_ip.

    The service runs behind nginx, so request.client.host is always 127.0.0.1;
    read the first hop of X-Forwarded-For first.
    """
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.headers.get("x-real-ip") or (
        request.client.host if request.client else ""
    )


@router.post("/wechat/create")
async def create_wechat_payment(
    body: CreateWechatPayRequest,
    request: Request,
    current_user: UserORM = Depends(get_current_user),
):
    """Create a WeChat Pay order.

    scene='h5' (mobile browser) returns h5_url, which the frontend redirects to in order to launch the WeChat app;
    scene='native' (default, desktop) returns code_url for rendering a QR code.

    If the merchant account has not enabled "H5 payment" yet, no error — it automatically falls back to a QR order and includes
    fallback='h5_not_enabled' in the response, so the frontend switches to "long-press to scan / pay on desktop" guidance.
    Once the permission is enabled it switches to redirects automatically, with no code change.
    """
    if (body.scene or "native").lower() == "h5":
        try:
            return wechat_pay_service.create_h5_order(
                user=current_user,
                membership_slug=body.slug,
                client_ip=_client_ip(request),
                redirect_url=body.redirect_url,
            )
        except H5NotEnabled as e:
            logger.info(
                "H5 payment not enabled (%s), falling back to QR: user=%s tier=%s",
                e.code, current_user.email, body.slug,
            )
            result = wechat_pay_service.create_native_order(
                user=current_user,
                membership_slug=body.slug,
            )
            return {**result, "fallback": "h5_not_enabled"}

    return wechat_pay_service.create_native_order(
        user=current_user,
        membership_slug=body.slug,
    )


class JsapiCreateRequest(BaseModel):
    slug: str
    # code returned by the official account web authorization redirect (valid five minutes, single use)
    code: str


@router.get("/wechat/jsapi/config")
async def wechat_jsapi_config(redirect_uri: str = "", state: str = ""):
    """Tell the frontend whether JSAPI is usable, and which authorization URL to send the user to.

    Without the official account AppSecret configured, returns configured=false, so the frontend falls back to the QR code
    instead of taking the user into an authorization redirect that is bound to fail.
    """
    configured = wechat_pay_service.jsapi_configured()
    return {
        "configured": configured,
        "oauth_url": (
            wechat_pay_service.build_oauth_url(redirect_uri, state)
            if configured and redirect_uri
            else ""
        ),
    }


@router.post("/wechat/jsapi/create")
async def create_wechat_jsapi_payment(
    body: JsapiCreateRequest,
    current_user: UserORM = Depends(get_current_user),
):
    """Order inside the WeChat built-in browser, returning the signed parameters WeixinJSBridge needs to launch payment.

    No error when AppSecret is not configured — falls back to a QR order with fallback, and the frontend switches to long-press guidance.
    """
    try:
        openid = wechat_pay_service.exchange_code_for_openid(body.code)
        return wechat_pay_service.create_jsapi_order(
            user=current_user, membership_slug=body.slug, openid=openid
        )
    except JsapiNotConfigured:
        logger.info(
            "JSAPI not configured (missing official account AppSecret), falling back to QR: user=%s tier=%s",
            current_user.email, body.slug,
        )
        result = wechat_pay_service.create_native_order(
            user=current_user, membership_slug=body.slug
        )
        return {**result, "fallback": "jsapi_not_configured"}


@router.get("/wechat/status/{payment_id}")
async def get_wechat_status(
    payment_id: int,
    current_user: UserORM = Depends(get_current_user),
):
    """Poll payment status. Optionally queries WeChat API as safety net."""
    db = SessionLocal()
    try:
        row = (
            db.query(PaymentSessionORM)
            .filter(
                PaymentSessionORM.id == payment_id,
                PaymentSessionORM.user_id == current_user.id,
                PaymentSessionORM.provider == "wechat",
            )
            .first()
        )
        if not row:
            raise AppException(status_code=404, message="Payment not found")

        # If still pending, try querying WeChat API as a safety net
        if row.status == "pending" and row.stripe_session_id:
            try:
                wx_result = wechat_pay_service.query_order(row.stripe_session_id)
                if wx_result.get("trade_state") == "SUCCESS":
                    wechat_pay_service._fulfill_if_needed(row.stripe_session_id)
                    # Re-query — _fulfill_if_needed uses its own DB session
                    db.expire(row)
                    row = (
                        db.query(PaymentSessionORM)
                        .filter(PaymentSessionORM.id == payment_id)
                        .first()
                    )
            except Exception:
                pass  # Safety net — don't fail the polling endpoint

        return {
            "payment_id": row.id,
            "status": row.status,
            "completed_at": str(row.completed_at) if row.completed_at else None,
        }
    finally:
        db.close()


@router.post("/wechat/cancel/{payment_id}")
async def cancel_wechat_payment(
    payment_id: int,
    current_user: UserORM = Depends(get_current_user),
):
    """Cancel a pending WeChat Pay session so the caller can initiate a fresh
    one. Safe — QR not scanned means no money has moved. Only affects rows
    owned by the caller that are still pending.
    """
    db = SessionLocal()
    try:
        row = (
            db.query(PaymentSessionORM)
            .filter(
                PaymentSessionORM.id == payment_id,
                PaymentSessionORM.user_id == current_user.id,
                PaymentSessionORM.provider == "wechat",
            )
            .first()
        )
        if not row:
            raise AppException(status_code=404, message="Payment not found")
        if row.status != "pending":
            raise AppException(
                status_code=400,
                message=f"Cannot cancel a {row.status} payment",
            )
        row.status = "canceled"
        db.commit()
        return {"payment_id": row.id, "status": row.status}
    finally:
        db.close()


@router.post("/wechat/notify")
async def wechat_notify(request: Request):
    """WeChat Pay V3 async notification callback.

    No auth required — WeChat sends this directly. The service layer
    verifies the notification by decrypting with the APIv3 key.
    """
    body = await request.body()
    headers = dict(request.headers)
    result = wechat_pay_service.handle_notify(body, headers)
    return result

class TopupRequest(BaseModel):
    amount_cents: int          # Top-up amount (RMB cents)


@router.post("/wechat/topup/create")
async def create_wechat_topup(
    body: TopupRequest,
    current_user: UserORM = Depends(get_current_user),
):
    """Web top-up: scan to top up the compute-points wallet. The callback routes the credit by purpose and activates no plan."""
    return wechat_pay_service.create_topup_native_order(
        user=current_user, amount_cents=int(body.amount_cents or 0)
    )
