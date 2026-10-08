"""Persistence models for WeChat QR login / SMS verification codes.

Why persist to DB instead of process memory: prod runs 4 uvicorn workers, the official account event callback
lands on a random worker, and frontend polling hits yet another worker. If any of scene state, verification codes or
the official account access_token lives in memory, it shows up as "scanned but nothing happens".
"""

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from geo.database import Base


class WechatLoginSessionORM(Base):
    """One QR login session = one scene of a parameterized QR code."""

    __tablename__ = "wechat_login_sessions"

    id = Column(Integer, primary_key=True, index=True)
    # QR scene_str, also the handle for frontend polling. WeChat limits it to 1-64 chars.
    scene = Column(String(length=64), unique=True, index=True, nullable=False)
    ticket = Column(Text, nullable=True)          # Ticket WeChat uses to fetch the QR image
    # pending  = QR issued, waiting for scan
    # confirmed= scanned and following, user identified (user_id set)
    # need_phone = scanned and following, but phone not yet bound (bind_token set)
    # consumed = already exchanged for a JWT, not reusable (one-time)
    # expired  = timed out
    status = Column(String(length=16), nullable=False, default="pending", index=True)
    openid = Column(String(length=64), index=True, nullable=True)
    unionid = Column(String(length=64), nullable=True)
    subscribed = Column(Boolean, nullable=False, default=False)
    user_id = Column(Integer, index=True, nullable=True)
    # Short-lived JWT issued for need_phone; the bind-phone endpoint uses it to identify the openid
    bind_token = Column(Text, nullable=True)
    client_ip = Column(String(length=64), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    scanned_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    consumed_at = Column(DateTime, nullable=True)


class SmsCodeORM(Base):
    """SMS verification code. Marked used on successful verification; failures increment attempts, and it is voided once over the limit."""

    __tablename__ = "sms_codes"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String(length=32), index=True, nullable=False)
    code = Column(String(length=8), nullable=False)
    purpose = Column(String(length=32), nullable=False, default="bind", index=True)
    used = Column(Boolean, nullable=False, default=False)
    attempts = Column(Integer, nullable=False, default=0)
    send_ip = Column(String(length=64), nullable=True)
    # WeChat openid. On the H5 channel, rate limiting counts by it instead of IP -- at a promo booth everyone shares
    # one egress IP, and per-IP limits would block them all (see sms_service.send_code).
    identity = Column(String(length=64), index=True, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    expires_at = Column(DateTime, nullable=False)


class WechatMpTokenORM(Base):
    """Global access_token cache for the official account.

    WeChat issues it globally per appid with a call quota; if each worker fetches its own they knock each other out
    (the old token is invalidated immediately), so a single shared copy is required.
    """

    __tablename__ = "wechat_mp_tokens"

    id = Column(Integer, primary_key=True)
    app_id = Column(String(length=64), unique=True, index=True, nullable=False)
    access_token = Column(Text, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
