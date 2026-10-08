"""SkillHub Pay Skill -- WeChat Agent Pay (X402) integration.

One paid call has four stages:

    (1) We create a WeChat Pay order (Native/JSAPI both work; existing capability)
    (2) Sign with the private key issued by SkillHub and call WeChat "AI pre-order" to get a payment_code
    (3) Hand the payment_code to the Agent via WeixinPay-Required in the Skill response
    (4) The Agent asks the user to authorize payment, then comes back to fetch the content

This module handles (2) and (3). (1) reuses wechat_pay_service; (4) is done by the Agent-side weixinpay plugin.

★ The signature is the easiest thing to get wrong; implemented verbatim per the docs:
  - The signing string is **always five lines**, each ending with \\n (0x0A), **including the last line**
  - Line 2 is the fixed path /palmpayminiapp/clawagentpay/preorder, no host, no query
  - Line 5 is the whole payment_required string after Base64
  - SHA256withRSA (PKCS#1 v1.5), result Base64
  - The pre-order API uses **body-only auth**: no Authorization header; all auth fields are in the body

★ Payment results **are authoritative only via the WeChat notify_url callback or an out_trade_no order query**, not Agent-side messages --
  the Agent saying "paid" doesn't count; it's only relaying the user.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import time
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

PREORDER_PATH = "/palmpayminiapp/clawagentpay/preorder"
PREORDER_URL = "https://payapp.weixin.qq.com" + PREORDER_PATH
SIGNATURE_TYPE = "SKILLHUB-SHA256-RSA2048"
DEVELOPER_PLATFORM = "SKILLHUB"
MAX_EXPIRE_SECONDS = 15 * 60          # per docs: payment_code lasts at most 15 minutes


class SkillPayNotConfigured(RuntimeError):
    """Merchant id or key not configured. **Don't pretend it works** -- upper layers must honestly tell the user to pay via the web."""


class SkillPayError(RuntimeError):
    pass


def developer_id() -> str:
    return (os.environ.get("SKILLHUB_DEVELOPER_ID") or "").strip()      # sh-XXXXXXXX


def pub_key_id() -> str:
    return (os.environ.get("SKILLHUB_PUB_KEY_ID") or "").strip()        # PUB_KEY_ + 32 HEX chars


def _private_key_pem() -> str:
    """Private key. Prefer reading from a file path (recommended: not in env vars, not in the process list), otherwise from an env var."""
    path = (os.environ.get("SKILLHUB_PRIVATE_KEY_PATH") or "").strip()
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read()
    return (os.environ.get("SKILLHUB_PRIVATE_KEY_PEM") or "").strip().replace("\\n", "\n")


def configured() -> bool:
    return bool(developer_id() and pub_key_id() and _private_key_pem())


def build_business_json(*, skill_id: str, skill_version: str, product_id: str,
                        pay_type_value: str, pay_data_type: str = "code_url",
                        expires_at: Optional[int] = None) -> dict:
    """L2 business JSON. pay_data.value is the WeChat order response filled in as-is:

    - Native order -> type=code_url,  value=weixin://wxpay/bizpayurl?pr=xxx
    - JSAPI/mini program/APP -> type=prepay_id, value=the prepay_id returned by the order
    """
    exp = int(expires_at or (time.time() + MAX_EXPIRE_SECONDS))
    return {
        "skill_info": {"skill_id": skill_id, "skill_version": skill_version},
        "pay_type": "SKILL_PAY",            # fixed
        "pay_mode": "AUTH_AND_PAY",         # fixed
        "pay_items": [{
            "product_id": product_id,
            "pay_data": {"type": pay_data_type, "value": pay_type_value},
        }],
        "expires_at": str(exp),
    }


def encode_payment_required(business: dict) -> str:
    """Standard Base64 (not URL-safe, no line breaks)."""
    raw = json.dumps(business, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def build_sign_string(timestamp: str, nonce_str: str, payment_required: str) -> str:
    """Five-line signing string, **every line ends with \\n, the last one too**. Missing the trailing newline fails signature verification."""
    return f"POST\n{PREORDER_PATH}\n{timestamp}\n{nonce_str}\n{payment_required}\n"


def sign(sign_string: str, private_key_pem: Optional[str] = None) -> str:
    """SHA256withRSA (PKCS#1 v1.5) -> Base64."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    pem = (private_key_pem if private_key_pem is not None else _private_key_pem())
    if not pem:
        raise SkillPayNotConfigured("缺少 SkillHub 开发者私钥")
    key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    sig = key.sign(sign_string.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode("ascii")


def build_request_body(business: dict, *, timestamp: Optional[str] = None,
                       nonce_str: Optional[str] = None,
                       private_key_pem: Optional[str] = None,
                       dev_id: Optional[str] = None,
                       key_id: Optional[str] = None) -> dict:
    """Assemble the L1 request body (with signature). Parameters are injectable so tests can cross-check with fixed values."""
    ts = timestamp or str(int(time.time()))
    nonce = nonce_str or secrets.token_hex(16)          # 32 random alphanumeric chars
    payment_required = encode_payment_required(business)
    signature = sign(build_sign_string(ts, nonce, payment_required), private_key_pem)
    return {
        "signature_type": SIGNATURE_TYPE,
        "developer_platform": DEVELOPER_PLATFORM,
        "developer_id": dev_id if dev_id is not None else developer_id(),
        "pub_key_id": key_id if key_id is not None else pub_key_id(),
        "nonce_str": nonce,
        "timestamp": ts,
        "signature": signature,
        "payment_required": payment_required,
    }


async def preorder(business: dict, *, timeout: float = 20.0) -> str:
    """Call AI pre-order and return payment_code. Body-only auth, no Authorization header."""
    if not configured():
        raise SkillPayNotConfigured("SkillHub 商户号或密钥未配置")
    body = build_request_body(business)
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(PREORDER_URL, json=body, headers={"Content-Type": "application/json"})
    if r.status_code != 200:
        log.warning("[skillpay] preorder HTTP %s: %s", r.status_code, r.text[:300])
        raise SkillPayError(f"预下单失败 HTTP {r.status_code}")
    data = r.json() or {}
    code = (data.get("payment_code") or data.get("data", {}).get("payment_code") or "").strip()
    if not code:
        log.warning("[skillpay] preorder has no payment_code: %s", str(data)[:300])
        raise SkillPayError("预下单返回里没有 payment_code")
    return code


def payment_required_response(payment_code: str, message: str = "本次查询需要付费") -> tuple[dict, dict]:
    """Response material for Step 4; returns (headers, body).

    **Provide both Header and Body**: the Header is the recommended way; the WeixinPay block in the Body is for compatibility with
    Agents that only read the body. HTTP status 402 or 200 both work; we use 402 (more accurate semantics).
    """
    headers = {"WeixinPay-Required": payment_code}
    body = {
        "data": {"message": message},
        "WeixinPay": {
            "WeixinPay-Required": payment_code,
            "prompt": ("本次使用微信支付,请将 WeixinPay-Required 的值作为 paymentCode "
                       "交给 weixinpay_pay,以向用户申请支付授权。"),
        },
    }
    return headers, body
