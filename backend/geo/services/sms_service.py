"""SMS verification codes -- phone number binding / phone login.

The channel is selected by SMS_PROVIDER:
- aliyun (default since 2026-09): reuses sales_agent's AliyunSmsProvider; signing logic maintained in one place only.
- tencent (old channel, kept for rollback): modeled on Zen7-Chat-Mini's app/utils/sms_util.py,
  **without tencentcloud-sdk-python**, signing directly with TC3-HMAC-SHA256 over httpx,
  saving a dependency of tens of MB (deployment machine disk is tight, see reference_deploy_scripts).

No credentials = channel not enabled; send_code() raises SmsNotConfigured, which the API layer turns into 503.
Never silently allow -- silently allowing means anyone could register with any phone number.

There are two sets of rate-limit keys: by default "phone number + egress IP"; with identity (WeChat openid)
it's "phone number + openid". The latter is for on-site promotion events; see send_code's docstring for why.
"""

import hashlib
import hmac
import json
import logging
import random
import time
from datetime import datetime, timedelta
from typing import List, Optional

import httpx
from sqlalchemy import func

from geo.database import SessionLocal, settings
from geo.models.wechat_auth import SmsCodeORM

logger = logging.getLogger("geo.sms")

HOST = "sms.tencentcloudapi.com"
SERVICE = "sms"
VERSION = "2021-01-11"
ACTION = "SendSms"

# Max wrong attempts allowed per code. Beyond that the code is voided and must be resent -- blocks brute-forcing 6-digit codes.
MAX_VERIFY_ATTEMPTS = 5


class SmsNotConfigured(Exception):
    """SMS credentials / signature / template not fully configured."""


class SmsRateLimited(Exception):
    """Sent too frequently or daily limit exceeded."""


def provider() -> str:
    return (settings.SMS_PROVIDER or "aliyun").strip().lower()


def is_configured() -> bool:
    if not (settings.SMS_SIGN_NAME and settings.SMS_TEMPLATE_ID):
        return False
    if provider() == "tencent":
        return bool(
            settings.TENCENT_SECRET_ID
            and settings.TENCENT_SECRET_KEY
            and settings.SMS_APP_ID
        )
    key_id, secret = _aliyun_ak()
    return bool(key_id and secret)


def _aliyun_ak():
    """SMS-specific AK first; fall back to the compliance external-review pair only if not configured."""
    if settings.SMS_ALIYUN_ACCESS_KEY_ID and settings.SMS_ALIYUN_ACCESS_KEY_SECRET:
        return settings.SMS_ALIYUN_ACCESS_KEY_ID, settings.SMS_ALIYUN_ACCESS_KEY_SECRET
    return settings.ALIBABA_CLOUD_ACCESS_KEY_ID, settings.ALIBABA_CLOUD_ACCESS_KEY_SECRET


def normalize_phone(raw: str) -> Optional[str]:
    """Normalize a phone number. Returns None if invalid; the caller reports the error.

    CN numbers normalize to 11 plain digits (existing users' users.phone is in this form and must not change);
    international numbers normalize to E.164 -- "+" + country code + number, e.g. "+85261234567".
    There's only one distinguishing rule: the raw input starts with "+" (or "00") and the country code isn't 86. Since 2026-09-15 the login page
    allows choosing a country (like the Qwen login page); before that everything was treated as a CN number.
    """
    text = (raw or "").strip()
    intl = text.startswith("+") or text.startswith("00")
    digits = "".join(ch for ch in text if ch.isdigit())
    if text.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0086"):
        digits = digits[4:]
        intl = False
    elif digits.startswith("86") and (intl or len(digits) == 13):
        digits = digits[2:]
        intl = False
    if not intl:
        if len(digits) == 11 and digits[0] == "1" and digits[1] in "3456789":
            return digits
        return None
    # International: E.164 total length 8~15 digits (including country code); country code can't start with 0
    if 8 <= len(digits) <= 15 and digits[0] != "0":
        return "+" + digits
    return None


def is_international(phone: str) -> bool:
    return (phone or "").startswith("+")


def _e164(phone: str) -> str:
    """Tencent Cloud PhoneNumberSet requires E.164 (with +); CN numbers get +86 prepended."""
    return phone if is_international(phone) else f"+86{phone}"


def _sms_sign_and_template(phone: str) -> tuple[str, str]:
    """International numbers use the international signature/template (both Aliyun and Tencent Cloud international SMS need separate registration); if not configured fall back to the CN pair,
    whether it gets delivered is up to the provider; failures are raised to the caller as-is."""
    if is_international(phone):
        return (settings.SMS_INTL_SIGN_NAME or settings.SMS_SIGN_NAME,
                settings.SMS_INTL_TEMPLATE_ID or settings.SMS_TEMPLATE_ID)
    return settings.SMS_SIGN_NAME, settings.SMS_TEMPLATE_ID


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _send_sms_sdk(phone: str, template_params: List[str]) -> None:
    """If tencentcloud-sdk-python is installed, use the official SDK (signing logic maintained officially)."""
    from tencentcloud.common import credential
    from tencentcloud.common.profile.client_profile import ClientProfile
    from tencentcloud.common.profile.http_profile import HttpProfile
    from tencentcloud.sms.v20210111 import models, sms_client

    http_profile = HttpProfile()
    http_profile.endpoint = HOST
    http_profile.reqTimeout = 15
    client_profile = ClientProfile()
    client_profile.httpProfile = http_profile
    client = sms_client.SmsClient(
        credential.Credential(settings.TENCENT_SECRET_ID, settings.TENCENT_SECRET_KEY),
        settings.SMS_REGION, client_profile,
    )
    req = models.SendSmsRequest()
    sign, tpl = _sms_sign_and_template(phone)
    req.SmsSdkAppId = settings.SMS_APP_ID
    req.SignName = sign
    req.TemplateId = tpl
    req.TemplateParamSet = template_params
    req.PhoneNumberSet = [_e164(phone)]
    resp = client.SendSms(req)
    for status in resp.SendStatusSet:
        if status.Code != "Ok":
            raise RuntimeError(
                f"短信下发失败 {status.PhoneNumber}: {status.Code} - {status.Message}"
            )


def _send_sms_aliyun(phone: str, template_params: List[str]) -> None:
    """Use Aliyun dysmsapi. Signing/requests are handled by sales_agent's adapter; this only assembles credentials.

    Aliyun template variables are named (${code}), not Tencent's positional {1}: SMS_TEMPLATE_PARAM_NAMES
    gives the name order; extra positional args (e.g. the template has no minutes field) are dropped, otherwise Aliyun
    rejects the send for passing variables not in the template.
    """
    from agents.sales_agent.service.sms_providers.aliyun import AliyunSmsProvider
    from agents.sales_agent.service.sms_providers.base import (
        SmsChannelCredentials, SmsProviderError,
    )

    names = [n.strip() for n in (settings.SMS_TEMPLATE_PARAM_NAMES or "code").split(",") if n.strip()]
    params = template_params[:len(names)]
    key_id, secret = _aliyun_ak()
    sign, tpl = _sms_sign_and_template(phone)
    creds = SmsChannelCredentials(
        provider="aliyun",
        sign_name=sign,
        api_key_id=key_id,
        api_secret=secret,
        provider_config={"region": settings.SMS_ALIYUN_REGION, "param_names": names},
    )
    # Aliyun PhoneNumbers: CN 11 digits; international/HK/Macau/Taiwan are "country code + number" **without +** (e.g. 85261234567)
    to = phone.lstrip("+") if is_international(phone) else phone
    try:
        AliyunSmsProvider().send(
            creds, to,
            template_code=tpl,
            template_params=params,
        )
    except SmsProviderError as exc:
        raise RuntimeError(f"阿里云短信失败 [{exc.code}]: {exc}") from exc


def _send_sms(phone: str, template_params: List[str]) -> None:
    """Actually send. Raises on failure; callers must not treat failure as success."""
    if provider() != "tencent":
        _send_sms_aliyun(phone, template_params)
        return
    _send_sms_tencent(phone, template_params)


def _send_sms_tencent(phone: str, template_params: List[str]) -> None:
    """Tencent Cloud (old channel)."""
    try:
        import tencentcloud  # noqa: F401
    except ImportError:
        pass
    else:
        _send_sms_sdk(phone, template_params)
        return

    secret_id = settings.TENCENT_SECRET_ID
    secret_key = settings.TENCENT_SECRET_KEY
    sign, tpl = _sms_sign_and_template(phone)
    payload = json.dumps({
        "PhoneNumberSet": [_e164(phone)],
        "SmsSdkAppId": settings.SMS_APP_ID,
        "SignName": sign,
        "TemplateId": tpl,
        "TemplateParamSet": template_params,
    }, separators=(",", ":"))

    timestamp = int(time.time())
    date = datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d")
    ct = "application/json; charset=utf-8"

    # (1) Canonical request string
    canonical_headers = f"content-type:{ct}\nhost:{HOST}\nx-tc-action:{ACTION.lower()}\n"
    signed_headers = "content-type;host;x-tc-action"
    canonical_request = "\n".join([
        "POST", "/", "",
        canonical_headers, signed_headers,
        hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    ])

    # (2) String to sign
    scope = f"{date}/{SERVICE}/tc3_request"
    string_to_sign = "\n".join([
        "TC3-HMAC-SHA256", str(timestamp), scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])

    # (3) Derive the signing key step by step
    secret_date = _sign(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = _sign(secret_date, SERVICE)
    secret_signing = _sign(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    authorization = (
        f"TC3-HMAC-SHA256 Credential={secret_id}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    with httpx.Client(timeout=15) as client:
        resp = client.post(
            f"https://{HOST}",
            content=payload.encode("utf-8"),
            headers={
                "Authorization": authorization,
                "Content-Type": ct,
                "Host": HOST,
                "X-TC-Action": ACTION,
                "X-TC-Version": VERSION,
                "X-TC-Timestamp": str(timestamp),
                "X-TC-Region": settings.SMS_REGION,
            },
        )
    body = resp.json().get("Response", {})
    if body.get("Error"):
        raise RuntimeError(f"腾讯云短信失败: {body['Error']}")
    for status in body.get("SendStatusSet", []):
        if status.get("Code") != "Ok":
            raise RuntimeError(
                f"短信下发失败 {status.get('PhoneNumber')}: "
                f"{status.get('Code')} - {status.get('Message')}"
            )


def send_code(phone: str, client_ip: Optional[str], purpose: str = "bind",
              identity: Optional[str] = None) -> None:
    """Generate and send a verification code. Raises if rate limits fail / channel isn't configured.

    `identity` (WeChat openid) is for **on-site promotion events**: everyone at a booth shares the same
    hotspot egress IP, so IP rate limits (8/hour / 30/day) collectively lock up at the 9th person,
    and nobody on site can tell why. openid is a strong identity on the WeChat side; having it means the person really
    completed official account web authorization -- far more trustworthy than IP, so with identity we count by openid
    and no longer look at IP.

    But **that doesn't mean unlimited**: one openid could still send one message each to a pile of strangers' numbers.
    The two per-phone limits (60s interval + 10 per number per day) stop "one number being bombed",
    not "one person bombing many numbers", so openid gets its own daily limit here.
    """
    if not (is_configured() or settings.SMS_DEBUG_ECHO):
        raise SmsNotConfigured("短信通道未开通")

    now = datetime.utcnow()
    db = SessionLocal()
    try:
        # Minimum resend interval for the same number
        last = (db.query(SmsCodeORM)
                  .filter(SmsCodeORM.phone == phone)
                  .order_by(SmsCodeORM.created_at.desc())
                  .first())
        if last and (now - last.created_at).total_seconds() < settings.SMS_RESEND_INTERVAL_SECONDS:
            wait = settings.SMS_RESEND_INTERVAL_SECONDS - int((now - last.created_at).total_seconds())
            raise SmsRateLimited(f"发送过于频繁,请 {max(wait, 1)} 秒后重试")

        day_ago = now - timedelta(days=1)
        phone_count = (db.query(func.count(SmsCodeORM.id))
                         .filter(SmsCodeORM.phone == phone, SmsCodeORM.created_at >= day_ago)
                         .scalar() or 0)
        if phone_count >= settings.SMS_DAILY_LIMIT_PER_PHONE:
            raise SmsRateLimited("该手机号今日验证码次数已达上限")

        if identity:
            id_count = (db.query(func.count(SmsCodeORM.id))
                          .filter(SmsCodeORM.identity == identity,
                                  SmsCodeORM.created_at >= day_ago)
                          .scalar() or 0)
            if id_count >= settings.SMS_DAILY_LIMIT_PER_IDENTITY:
                raise SmsRateLimited("今日验证码次数已达上限,请明天再试")
        elif client_ip:
            ip_count = (db.query(func.count(SmsCodeORM.id))
                          .filter(SmsCodeORM.send_ip == client_ip, SmsCodeORM.created_at >= day_ago)
                          .scalar() or 0)
            if ip_count >= settings.SMS_DAILY_LIMIT_PER_IP:
                raise SmsRateLimited("当前网络今日验证码次数已达上限")
            hour_ago = now - timedelta(hours=1)
            ip_hour = (db.query(func.count(SmsCodeORM.id))
                         .filter(SmsCodeORM.send_ip == client_ip,
                                 SmsCodeORM.created_at >= hour_ago)
                         .scalar() or 0)
            if ip_hour >= settings.SMS_HOURLY_LIMIT_PER_IP:
                raise SmsRateLimited("操作过于频繁,请稍后再试")

        code = f"{random.SystemRandom().randrange(0, 10 ** 6):06d}"
        ttl_minutes = max(settings.SMS_CODE_TTL_SECONDS // 60, 1)

        if settings.SMS_DEBUG_ECHO:
            logger.warning("[SMS_DEBUG_ECHO] verification code for %s is %s (not actually sent)", phone, code)
        else:
            _send_sms(phone, [code, str(ttl_minutes)])

        # Send first, then write: if the SMS didn't go out there shouldn't be a code that can verify
        db.add(SmsCodeORM(
            phone=phone,
            code=code,
            purpose=purpose,
            send_ip=client_ip,
            identity=identity or None,
            created_at=now,
            expires_at=now + timedelta(seconds=settings.SMS_CODE_TTL_SECONDS),
        ))
        db.commit()
    finally:
        db.close()


def verify_code(phone: str, code: str, purpose: str = "bind") -> bool:
    """Verify and consume a code. Each code can be used only once."""
    now = datetime.utcnow()
    db = SessionLocal()
    try:
        row = (db.query(SmsCodeORM)
                 .filter(SmsCodeORM.phone == phone,
                         SmsCodeORM.purpose == purpose,
                         SmsCodeORM.used.is_(False),
                         SmsCodeORM.expires_at > now)
                 .order_by(SmsCodeORM.created_at.desc())
                 .first())
        if not row:
            return False
        if row.attempts >= MAX_VERIFY_ATTEMPTS:
            row.used = True          # too many attempts, void it directly
            db.commit()
            return False
        if not hmac.compare_digest(row.code, (code or "").strip()):
            row.attempts += 1
            db.commit()
            return False
        row.used = True
        db.commit()
        return True
    finally:
        db.close()
