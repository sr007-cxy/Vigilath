"""embed token — issuance and validation of 1-year account tokens (docs/03-系统设计/Agent/对外开放-契约.md §12).

- Separate signing key `AGENT_EMBED_SECRET` (derived from the main SECRET_KEY if unset), `aud="agent-embed"` hard-isolated from login JWTs.
- Payload: iss / aud / sub (=account_id) / tid / caps / iat / exp.
- Validation **must check agent_tokens.enabled** (the revocation lifeline for long-lived tokens); it can't be purely stateless.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import jwt
from jwt import PyJWTError as JWTError
from sqlalchemy.orm import Session

from geo.database import settings
from geo.models.agent import AgentTokenORM

ALGORITHM = "HS256"
ISSUER = "vigilath-agent"
AUDIENCE = "agent-embed"


def _secret() -> str:
    # Separate secret; when not configured separately, derive a different value from the main SECRET_KEY (still hard-isolated via aud).
    return (os.environ.get("AGENT_EMBED_SECRET") or "").strip() or f"{settings.SECRET_KEY}::agent-embed"


class EmbedAuthError(Exception):
    """External token validation failure. code maps directly to the HTTP status."""

    def __init__(self, code: int, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


@dataclass
class EmbedClaims:
    account_id: int
    caps: list[str]
    tid: str
    origins: list[str]
    policy_key: str = ""      # Call policy tier specified for this token; empty = channel default tier


def _parse_caps(raw: str | None) -> list[str]:
    return [c.strip() for c in (raw or "").split(",") if c.strip()]


def issue_token(
    db: Session,
    account_id: int,
    caps: list[str] | None = None,
    label: str = "",
    origins: list[str] | None = None,
    ttl_days: int = 365,
) -> tuple[str, str]:
    """Mint: issue a 1-year token for an account. Returns (plaintext token, tid). The DB stores only tid + metadata."""
    caps = [c for c in (caps or ["read"]) if c in ("read", "write")] or ["read"]
    tid = "tok_" + uuid.uuid4().hex[:24]
    now = datetime.utcnow()
    exp = now + timedelta(days=ttl_days)

    db.add(AgentTokenORM(
        tid=tid,
        account_id=account_id,
        caps=",".join(caps),
        origins=",".join(origins or []),
        label=label,
        enabled=1,
        expires_at=exp,
        created_at=now,
    ))
    db.commit()

    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": str(account_id),
        "tid": tid,
        "caps": caps,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    token = jwt.encode(payload, _secret(), algorithm=ALGORITHM)
    return token, tid


def verify_token(db: Session, token: str) -> EmbedClaims:
    """Per-request validation: verify signature + aud + exp (PyJWT does it automatically) -> check enabled -> return claims. Raises EmbedAuthError on failure."""
    if not token:
        raise EmbedAuthError(401, "missing token")
    try:
        payload = jwt.decode(token, _secret(), algorithms=[ALGORITHM], audience=AUDIENCE, issuer=ISSUER)
    except JWTError as e:
        raise EmbedAuthError(401, f"invalid_token: {e}")

    tid = payload.get("tid")
    sub = payload.get("sub")
    if not tid or not sub:
        raise EmbedAuthError(401, "invalid_token: missing tid/sub")

    # Revocation lifeline for long-lived tokens: always check the DB row is still enabled and not expired.
    row = db.query(AgentTokenORM).filter(AgentTokenORM.tid == tid).first()
    if row is None or row.enabled != 1:
        raise EmbedAuthError(401, "invalid_token: revoked or unknown")
    if row.expires_at and row.expires_at < datetime.utcnow():
        raise EmbedAuthError(401, "invalid_token: expired")

    # caps follow the DB (can be narrowed without re-signing the token); intersected with the JWT, only narrowing, never widening.
    db_caps = set(_parse_caps(row.caps))
    jwt_caps = set(payload.get("caps") or [])
    caps = sorted(db_caps & jwt_caps) if jwt_caps else sorted(db_caps)
    return EmbedClaims(
        account_id=int(sub),
        caps=caps,
        tid=tid,
        origins=_parse_caps(row.origins),
        # The tier follows the DB (same as caps: tier can be changed / tightened without re-signing the token)
        policy_key=(row.policy_key or "").strip(),
    )
