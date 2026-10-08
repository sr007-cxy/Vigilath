"""Phone number + SMS code login/registration.

A separate channel from WeChat QR: a number that does not exist registers, an existing one logs in; the two entries are not distinguished
(common practice for CN products — making users first think "have I registered before" is a needless step).

The biggest security difference from QR: **the send-code endpoint is public**, with no bind_token guarding it,
so it is a natural target for SMS bombing. Four layers of rate limiting are stacked in sms_service:
    same number 60s interval / same number 10 per day / same IP 8 per hour / same IP 30 per day
The code itself also has a 5-attempt limit and is voided when exceeded (blocking enumeration of 6-digit codes).
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Request
import jwt
from pydantic import BaseModel

from geo.database import settings
from geo.models.user import Token
from geo.services import sms_service
from geo.services.user_service import user_service
from geo.utils.error_handler import AppException

logger = logging.getLogger("geo.phone_auth")

router = APIRouter()

SECRET_KEY = settings.SECRET_KEY
ALGORITHM = "HS256"
# Consistent with auth.py / wechat_auth.py (7 days)
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7

SMS_PURPOSE = "login"


class SendCodeIn(BaseModel):
    phone: str


class PhoneLoginIn(BaseModel):
    phone: str
    code: str


def _client_ip(request: Request) -> Optional[str]:
    # nginx sits in front; reading request.client directly would only give 127.0.0.1
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else None


def _issue_access_token(email: str) -> str:
    return jwt.encode(
        {"sub": email, "exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)},
        SECRET_KEY, algorithm=ALGORITHM,
    )


def _require_enabled() -> None:
    if not settings.PHONE_LOGIN_ENABLED:
        raise AppException(status_code=503, message="手机号登录暂未开通")
    if not (sms_service.is_configured() or settings.SMS_DEBUG_ECHO):
        raise AppException(status_code=503, message="短信服务暂未开通")


@router.post("/sms/send-login")
async def send_login_code(body: SendCodeIn, request: Request):
    """Send a login verification code.

    **Does not reveal whether the number is registered** — new and existing users get the same response, otherwise this endpoint becomes
    an enumeration tool for "is this phone number on your platform".
    """
    _require_enabled()

    phone = sms_service.normalize_phone(body.phone)
    if not phone:
        raise AppException(status_code=400, message="请输入正确的手机号")

    try:
        sms_service.send_code(phone, _client_ip(request), purpose=SMS_PURPOSE)
    except sms_service.SmsNotConfigured:
        raise AppException(status_code=503, message="短信服务暂未开通")
    except sms_service.SmsRateLimited as exc:
        raise AppException(status_code=429, message=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to send login verification code phone=%s: %s", phone, exc)
        raise AppException(status_code=502, message="验证码发送失败,请稍后重试")

    return {"sent": True, "resend_after": settings.SMS_RESEND_INTERVAL_SECONDS}


@router.post("/phone-login", response_model=Token)
async def phone_login(body: PhoneLoginIn):
    """Code verified → existing user logs in / new number registers; both get a JWT directly."""
    _require_enabled()

    phone = sms_service.normalize_phone(body.phone)
    if not phone:
        raise AppException(status_code=400, message="请输入正确的手机号")
    if not sms_service.verify_code(phone, body.code, purpose=SMS_PURPOSE):
        raise AppException(status_code=400, message="验证码错误或已过期")

    user = user_service.get_user_by_phone(phone)
    if user is None:
        user = user_service.create_phone_user(phone)
        logger.info("New account registered via phone number: id=%s", user.id)

    if not user.is_active:
        raise AppException(status_code=403, message="账号已被停用,请联系客服")

    return {"access_token": _issue_access_token(user.email), "token_type": "bearer"}
