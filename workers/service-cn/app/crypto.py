"""Credential encryption - AES (Fernet) symmetric encryption, key derived from env ENGINE_CRED_KEY.

Used to encrypt account credentials (identifier+password) before storing them in engine_sessions.credentials,
for decryption and re-login during automatic renewal after disconnects. Plaintext passwords only exist in memory/at login time; the DB stores ciphertext.
"""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Optional


def _fernet():
    raw = os.environ.get("ENGINE_CRED_KEY", "").strip()
    if not raw:
        return None
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return None
    # Derive a 32-byte urlsafe-base64 Fernet key from an arbitrary env secret
    key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
    return Fernet(key)


def enabled() -> bool:
    return _fernet() is not None


def encrypt(plaintext: str) -> Optional[str]:
    f = _fernet()
    if not f or plaintext is None:
        return None
    return f.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> Optional[str]:
    f = _fernet()
    if not f or not ciphertext:
        return None
    try:
        return f.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except Exception:  # noqa: BLE001
        return None
