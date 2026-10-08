"""Publish login session encryption -- Fernet, key derived from env `PUBLISH_CRED_KEY`.

Same derivation as `workers/service-cn/app/crypto.py` (SHA-256 -> urlsafe-b64 ->
Fernet; an env key of any length works), but the two are **deliberately different**:

1. **Separate key `PUBLISH_CRED_KEY`, not reusing `ENGINE_CRED_KEY`.**
   A leaked engine cookie can only query; a leaked publish cookie can **publish under someone's name** (plan section F2) --
   different blast radius, so the keys should be separate.

2. **Hard failure, no silent degradation.** The browser-service version returns None when `cryptography` is missing / no key is configured
   (credentials simply aren't stored, acceptable). Publish login sessions must never do that: silent degradation means plaintext
   persisted to DB or the account never stored at all, and both are only discovered when something goes wrong. Hence `encrypt_required()` /
   `decrypt_required()`, which raise `PublishCryptoUnavailable` directly when unconfigured; the API layer turns it into 503.

`cryptography>=41.0` is already in backend/requirements.txt:18; no new dependency needed.

## How to set the key

    # backend/.env
    PUBLISH_CRED_KEY=<any long random string; openssl rand -base64 48 recommended>

🔴 Changing this key turns every persisted `storage_state_enc` into undecryptable garbage --
every account must re-import its login session. To rotate, first write a migration script "decrypt with old key -> encrypt with new key".
"""
from __future__ import annotations

import base64
import hashlib
import os
from typing import Optional

_ENV_KEY = "PUBLISH_CRED_KEY"


class PublishCryptoUnavailable(RuntimeError):
    """PUBLISH_CRED_KEY not configured, or cryptography unavailable."""


def _fernet():
    raw = (os.environ.get(_ENV_KEY) or "").strip()
    if not raw:
        return None
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return None
    key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
    return Fernet(key)


def enabled() -> bool:
    return _fernet() is not None


def encrypt_required(plaintext: str) -> str:
    """Encrypt; raises directly if the key isn't configured. **Login sessions may only be persisted through this function.**"""
    f = _fernet()
    if f is None:
        raise PublishCryptoUnavailable(
            f"{_ENV_KEY} 未配置(或 cryptography 未安装)——"
            f"拒绝以明文存储发布登录态。请在 backend/.env 设置 {_ENV_KEY}。"
        )
    if plaintext is None:
        raise ValueError("nothing to encrypt")
    return f.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_required(ciphertext: str) -> str:
    """Decrypt; raises if the key isn't configured or the ciphertext is corrupt.

    When ciphertext can't be decrypted it **raises rather than returning None**: the caller is check-out, and without a login session it can't dispatch work.
    Returning None would make upper layers hand an empty storage_state to the browser -> start a logged-out session -> leave
    a "logged-out visit" record on the platform side, and waste a quota slot too.
    """
    f = _fernet()
    if f is None:
        raise PublishCryptoUnavailable(f"{_ENV_KEY} 未配置,无法解密发布登录态")
    if not ciphertext:
        raise ValueError("empty ciphertext")
    try:
        return f.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except PublishCryptoUnavailable:
        raise
    except Exception as e:  # noqa: BLE001
        # Most common cause: PUBLISH_CRED_KEY was rotated. Put that possibility in the exception message,
        # so troubleshooting doesn't start by guessing "the cookie is broken".
        raise ValueError(
            f"发布登录态解密失败({type(e).__name__})——"
            f"最常见原因是 {_ENV_KEY} 被更换过,需重新导入登录态"
        ) from e


def try_decrypt(ciphertext: Optional[str]) -> Optional[str]:
    """Only for scenarios where "can continue even if undecryptable" (e.g. an admin page that only shows whether the cookie decrypts)."""
    if not ciphertext:
        return None
    try:
        return decrypt_required(ciphertext)
    except Exception:  # noqa: BLE001
        return None
