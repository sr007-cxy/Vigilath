"""Sales Agent service layer: channel-agnostic foundation for outreach channels.

Email and SMS share the same logic: credential encryption, scope (global/user) normalization, and picking one of several channels to send with.
These three concerns have nothing to do with "email or SMS", only with the ORM class, so they are parameterized once here
and `email_config.py` and `sms_config.py` each delegate to them, instead of copying 800 lines of config code a second time.

**Do not put anything channel-specific here** (SMTP port normalization, SMS signature validation, template slots);
those stay in their own config modules. Put one in here and the next channel has to work around it.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
from datetime import datetime
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func
from sqlalchemy.orm import Session

from geo.database import settings

from agents.sales_agent.service.common import SalesAgentError

# Delivery statuses counted for rotation: exclude draft/disabled/no_recipient rows that "never actually used the channel",
# otherwise a channel that never really sent anything would be judged "sent the most" from piling up no_recipient rows and never get its turn.
CHANNEL_ROUND_ROBIN_STATUSES = ("sending", "sent", "failed")

CHANNEL_SENDER_SELECTION_POLICIES = {"default_only", "round_robin"}

_SENDER_POLICY_ALIASES = {
    "default": "default_only",
    "default_account": "default_only",
    "default_first": "default_only",
    "roundrobin": "round_robin",
    "rotation": "round_robin",
    "rotate": "round_robin",
}

_SECRET_KEY_PATTERN = re.compile(r"(secret|password|token|api[_-]?key|密钥|密码|授权码)", flags=re.IGNORECASE)


def channel_callback_token(kind: str, account_id: int) -> str:
    """The token segment in the receipt webhook URL: `{account_id}.{signature}`.

    Not stored in the DB. `sanitize_channel_provider_config` drops any key whose name contains token
    (that gate prevents secrets being echoed in plaintext), so provider_config cannot hold it; deriving it from SECRET_KEY
    is simpler anyway: there is no "generated once, forgot to save, mismatch" intermediate state.

    account_id is in plaintext at the front so the verifier can fetch the row by id and then compare the signature, without a full table scan.
    """
    aid = int(account_id or 0)
    if aid <= 0:
        return ""
    material = str(getattr(settings, "SECRET_KEY", "") or "") or "sales-agent-email-dev-key"
    sig = hmac.new(material.encode("utf-8"), f"{kind}:{aid}".encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return f"{aid}.{sig}"


def verify_channel_callback_token(kind: str, token: str) -> int:
    """Return account_id if valid, otherwise 0. Constant-time comparison, does not leak prefix match length."""
    raw = str(token or "").strip()
    if "." not in raw:
        return 0
    head, _, _tail = raw.partition(".")
    if not head.isdigit():
        return 0
    aid = int(head)
    return aid if hmac.compare_digest(channel_callback_token(kind, aid), raw) else 0


def channel_credential_fernet() -> Fernet:
    material = str(getattr(settings, "SECRET_KEY", "") or "") or "sales-agent-email-dev-key"
    key = base64.urlsafe_b64encode(hashlib.sha256(material.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_channel_secret(secret: str) -> str:
    text = str(secret or "").strip()
    if not text:
        return ""
    return channel_credential_fernet().encrypt(text.encode("utf-8")).decode("utf-8")


def decrypt_channel_secret(encrypted: str, error_message: str) -> str:
    """If decryption fails, raise a business error carrying the channel's own wording.

    `error_message` must say clearly "what to re-enter where": decryption only fails after SECRET_KEY has changed,
    so the user should see "re-save the authorization code", not "invalid key".
    """
    token = str(encrypted or "").strip()
    if not token:
        return ""
    try:
        return channel_credential_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise SalesAgentError(error_message, status_code=503) from e


def normalize_channel_scope(scope: str, label: str = "通道配置") -> str:
    value = str(scope or "user").strip().lower()
    if value not in {"global", "user"}:
        raise SalesAgentError(f"{label} scope 只能是 global 或 user。")
    return value


def channel_scope_user_id(scope: str, user_id: int | None) -> int | None:
    return None if scope == "global" else int(user_id or 0)


def normalize_channel_sender_policy(value: str, label: str = "发送调度策略") -> str:
    policy = str(value or "default_only").strip().lower().replace("-", "_")
    policy = _SENDER_POLICY_ALIASES.get(policy, policy)
    if policy not in CHANNEL_SENDER_SELECTION_POLICIES:
        raise SalesAgentError(f"{label}只能是 default_only 或 round_robin。")
    return policy


def sanitize_channel_provider_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """provider_config_json is a plaintext column shown to the frontend; drop any key that looks like a secret.

    Real secrets go through `*_encrypted` columns; letting an `api_key` through here would echo it in plaintext on the admin page.
    """
    clean: dict[str, Any] = {}
    for key, value in (config or {}).items():
        k = str(key or "").strip()[:64]
        if not k or _SECRET_KEY_PATTERN.search(k):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            clean[k] = value if not isinstance(value, str) else value[:1000]
    return clean


def channel_account_scope_query(db: Session, account_orm: Any, scope: str, user_id: int | None, label: str = "通道配置"):
    scope = normalize_channel_scope(scope, label)
    uid = channel_scope_user_id(scope, user_id)
    q = db.query(account_orm).filter(account_orm.scope == scope)
    return q.filter(account_orm.user_id.is_(None)) if uid is None else q.filter(account_orm.user_id == uid)


def enabled_channel_accounts_for_scope(db: Session, account_orm: Any, scope: str, user_id: int | None) -> list[Any]:
    return (
        channel_account_scope_query(db, account_orm, scope, user_id)
        .filter(account_orm.enabled.is_(True))
        .order_by(account_orm.is_default.desc(), account_orm.id.asc())
        .all()
    )


def pick_channel_default_account(
    db: Session,
    account_orm: Any,
    scope: str,
    user_id: int | None,
    default_account_id: int | None = None,
) -> Any | None:
    """Three-level fallback: the one pinned in settings -> the one marked is_default -> any enabled one."""
    if default_account_id:
        row = channel_account_scope_query(db, account_orm, scope, user_id).filter(
            account_orm.id == int(default_account_id),
            account_orm.enabled.is_(True),
        ).first()
        if row is not None:
            return row
    row = channel_account_scope_query(db, account_orm, scope, user_id).filter(
        account_orm.enabled.is_(True),
        account_orm.is_default.is_(True),
    ).order_by(account_orm.id.desc()).first()
    if row is not None:
        return row
    return channel_account_scope_query(db, account_orm, scope, user_id).filter(
        account_orm.enabled.is_(True)
    ).order_by(account_orm.id.desc()).first()


def pick_channel_round_robin_account(
    db: Session,
    account_orm: Any,
    delivery_orm: Any,
    account_fk: Any,
    scope: str,
    user_id: int | None,
    default_account_id: int | None = None,
    candidates: list[Any] | None = None,
) -> Any | None:
    """Pick the one that has sent the least and longest ago, spreading volume across channels so no single account gets hammered.

    `candidates` lets the caller pre-filter (SMS filters region_scope by the number's country code);
    when given, rotation happens only within that set; otherwise all enabled channels in the scope are used.
    """
    accounts = candidates if candidates is not None else enabled_channel_accounts_for_scope(db, account_orm, scope, user_id)
    if not accounts:
        return None
    if len(accounts) == 1:
        return accounts[0]
    ids = [int(row.id) for row in accounts]
    stats_rows = (
        db.query(account_fk, func.count(delivery_orm.id), func.max(delivery_orm.created_at))
        .filter(
            account_fk.in_(ids),
            delivery_orm.message_type != "test",
            delivery_orm.status.in_(list(CHANNEL_ROUND_ROBIN_STATUSES)),
        )
        .group_by(account_fk)
        .all()
    )
    stats = {int(account_id): (int(count or 0), last_at) for account_id, count, last_at in stats_rows if account_id is not None}
    default_account = pick_channel_default_account(db, account_orm, scope, user_id, default_account_id)
    default_id = int(default_account.id) if default_account is not None else 0
    return sorted(
        accounts,
        key=lambda row: (
            stats.get(int(row.id), (0, None))[0],
            stats.get(int(row.id), (0, None))[1] or datetime.min,
            0 if int(row.id) == default_id else 1,
            int(row.id),
        ),
    )[0]


def pick_channel_sender_account(
    db: Session,
    account_orm: Any,
    delivery_orm: Any,
    account_fk: Any,
    scope: str,
    user_id: int | None,
    policy: str = "default_only",
    default_account_id: int | None = None,
    candidates: list[Any] | None = None,
) -> Any | None:
    if policy == "round_robin":
        return pick_channel_round_robin_account(
            db, account_orm, delivery_orm, account_fk, scope, user_id,
            default_account_id=default_account_id, candidates=candidates,
        )
    if candidates is not None:
        # Already filtered once by channel conditions (e.g. SMS country-code routing), so we must not go back to the full set,
        # or we might pick a channel that cannot reach this number at all.
        default_id = int(default_account_id or 0)
        preferred = next((row for row in candidates if int(row.id) == default_id), None)
        if preferred is not None:
            return preferred
        return next((row for row in candidates if bool(row.is_default)), None) or (candidates[0] if candidates else None)
    return pick_channel_default_account(db, account_orm, scope, user_id, default_account_id)
