"""/api/agent/v1/wallet/* — agent-side wallet: check balance + **agent pays directly to top up**.

Why it exists: once paid capabilities (media publishing, drafting) really charge money, an agent hitting insufficient balance mid-conversation is stuck.
Sending the user to the web page to top up breaks half of that flow. So give the agent a path it can complete on its own.

Payment uses an x402-style two-step flow:

    ① agent POST /wallet/topup {amount_cents}  -> **402** + accepts (pay where, how much, which asset)
    ② agent transfers USDC on-chain then retries with X-PAYMENT -> verify on-chain receipt -> credit -> 200

★ Differences from standard x402, spelled out so the other agent doesn't guess: the standard approach has the client sign an EIP-3009
  authorization and a facilitator settles it; we have no facilitator, so we require **putting it on-chain first, then bringing
  the tx_hash to redeem**. For the agent that's one extra wait for confirmation, but we don't need to custody any signatures.

★ Amount conversion: the wallet records **RMB cents**, while on-chain we receive USDC (USD, 6 decimals).
  Conversion uses the single source settings.USD_TO_CNY_RATE — page prices, WeChat QR amounts and this must share the source,
  otherwise one top-up shows three different numbers in three places.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from decimal import ROUND_CEILING, Decimal

import httpx
from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from agents.geo.embed.tokens import EmbedClaims
from geo.database import SessionLocal, settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/agent/v1/wallet")

USDC_CONTRACT = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"   # Base mainnet USDC
BASE_RPC = os.environ.get("BASE_RPC_URL") or "https://mainnet.base.org"
USDC_DECIMALS = 6
MIN_TOPUP_CENTS = 1000        # Minimum top-up 10 CNY: given on-chain fees and redemption cost, anything smaller isn't worth it
MAX_TOPUP_CENTS = 5_000_00    # Per-transaction cap 5000 CNY; above that use the web page / corporate transfer


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _claims(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> EmbedClaims:
    from fastapi import HTTPException

    from agents.geo.embed.tokens import EmbedAuthError, verify_token

    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    try:
        return verify_token(db, token)
    except EmbedAuthError as e:
        raise HTTPException(status_code=e.code, detail=e.detail)


def _agent_topup_enabled() -> bool:
    """Separate gate for agent direct payment, **off** by default.

    ★ Why we can't rely on "whether the address is configured": MOLTSPAY_WALLET_ADDRESS is used by membership plan payment
      and has long been configured in prod. If the address were the criterion, this module would be ready the moment it's deployed to prod —
      and when test and prod share the same receiving address, **the same on-chain transfer could be redeemed once on each side**
      (the idempotency key onchain:<tx> is only unique within each DB), yielding free credit.
      So the enabled site must be declared explicitly, and only one site should be enabled at a time unless each uses its own receiving address.
    """
    return (os.environ.get("AGENT_TOPUP_ENABLED", "0") or "").strip().lower() in ("1", "true", "yes")


def _pay_to() -> str:
    return (getattr(settings, "MOLTSPAY_WALLET_ADDRESS", "") or "").strip()


def cny_cents_to_usdc_units(cents: int) -> int:
    """RMB cents -> USDC base units (6 decimals). **Round up** — we don't absorb exchange-rate fractions."""
    rate = Decimal(str(getattr(settings, "USD_TO_CNY_RATE", 7.2) or 7.2))
    usd = (Decimal(cents) / Decimal(100)) / rate
    return int((usd * (10 ** USDC_DECIMALS)).to_integral_value(rounding=ROUND_CEILING))


async def _tx_receipt(tx_hash: str) -> dict | None:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(BASE_RPC, json={"jsonrpc": "2.0", "method": "eth_getTransactionReceipt",
                                         "params": [tx_hash], "id": 1})
        if r.status_code != 200:
            return None
        return (r.json() or {}).get("result")


def _paid_usdc_to(receipt: dict, to_addr: str) -> int:
    """Count the total USDC transferred to us in the receipt (base units). Only USDC contract Transfer events count."""
    total = 0
    want = to_addr.lower().replace("0x", "").rjust(64, "0")
    for lg in receipt.get("logs") or []:
        if (lg.get("address") or "").lower() != USDC_CONTRACT.lower():
            continue
        topics = lg.get("topics") or []
        if len(topics) < 3:
            continue
        if (topics[2] or "").lower().replace("0x", "").rjust(64, "0") != want:
            continue        # Recipient isn't us, doesn't count
        try:
            total += int(lg.get("data") or "0x0", 16)
        except ValueError:
            continue
    return total


class TopupIn(BaseModel):
    amount_cents: int          # Desired top-up amount (RMB cents)


@router.get("")
async def wallet(claims: EmbedClaims = Depends(_claims), db: Session = Depends(get_db)):
    """Check balance. The agent should look at it before money-spending actions."""
    from geo.services import balance_service

    return {
        "account_id": claims.account_id,
        "balance_cents": balance_service.get_balance(db, claims.account_id),
        "currency": "CNY",
        "topup": {
            "agent": "POST /api/agent/v1/wallet/topup" if _agent_topup_enabled() else None,
            "web": "/account/balance",
        },
    }


@router.post("/topup")
async def topup(
    body: TopupIn,
    request: Request,
    x_payment: str = Header(default="", alias="X-PAYMENT"),
    claims: EmbedClaims = Depends(_claims),
    db: Session = Depends(get_db),
):
    """Top up. **Without X-PAYMENT, return 402 + payment requirements; with it, redeem the on-chain transaction and credit the account.**"""
    from fastapi.responses import JSONResponse

    from geo.services import balance_service

    cents = int(body.amount_cents or 0)
    if cents < MIN_TOPUP_CENTS or cents > MAX_TOPUP_CENTS:
        return JSONResponse(status_code=400, content={
            "error": "invalid_amount",
            "message": f"单笔充值范围 ¥{MIN_TOPUP_CENTS / 100:.0f}–¥{MAX_TOPUP_CENTS / 100:.0f}",
        })
    pay_to = _pay_to() if _agent_topup_enabled() else ""
    if not pay_to:
        return JSONResponse(status_code=503, content={
            "error": "agent_payment_unavailable",
            "message": "本站未开通 agent 直付,请到网页充值",
            "web": "/account/balance",
        })

    need_units = cny_cents_to_usdc_units(cents)

    # ── Step 1: no payment credential -> 402, tell the agent the payment requirements in structured form
    if not x_payment.strip():
        return JSONResponse(status_code=402, content={
            "x402Version": 1,
            "error": "payment_required",
            "accepts": [{
                "scheme": "exact-onchain",          # Non-standard: requires on-chain first, then redemption; see the module docstring
                "network": "base",
                "asset": USDC_CONTRACT,
                "assetSymbol": "USDC",
                "payTo": pay_to,
                "maxAmountRequired": str(need_units),
                "amountDecimals": USDC_DECIMALS,
                "resource": str(request.url),
                "description": f"Vigilath 钱包充值 ¥{cents / 100:.2f}",
                "maxTimeoutSeconds": 900,
            }],
            "howToPay": (
                "在 Base 链上把上述数量的 USDC 转到 payTo,拿到 tx_hash 后,"
                "带 X-PAYMENT 头重试本请求:X-PAYMENT = base64(JSON {\"tx_hash\":\"0x...\"})。"
                "也可以让用户到网页充值:/account/balance"
            ),
        })

    # ── Step 2: redemption
    try:
        raw = base64.b64decode(x_payment.strip() + "==", validate=False).decode("utf-8", "ignore")
        payload = json.loads(raw)
        tx_hash = str(payload.get("tx_hash") or "").strip()
    except Exception:  # noqa: BLE001
        tx_hash = x_payment.strip() if x_payment.strip().startswith("0x") else ""
    if not tx_hash.startswith("0x"):
        return JSONResponse(status_code=400, content={
            "error": "bad_payment_header",
            "message": "X-PAYMENT 应为 base64(JSON {\"tx_hash\":\"0x...\"}),或直接给 0x 开头的交易哈希",
        })

    receipt = await _tx_receipt(tx_hash)
    if not receipt:
        return JSONResponse(status_code=402, content={
            "error": "tx_not_found",
            "message": "链上查不到这笔交易(可能还没确认),稍等几秒再带同一个 tx_hash 重试",
        })
    if str(receipt.get("status", "0x0")).lower() not in ("0x1", "1"):
        return JSONResponse(status_code=402, content={"error": "tx_failed", "message": "该交易在链上失败"})

    paid = _paid_usdc_to(receipt, pay_to)
    if paid < need_units:
        return JSONResponse(status_code=402, content={
            "error": "insufficient_payment",
            "message": f"收到 {paid} 个 USDC 最小单位,需要 {need_units}",
            "received": paid, "required": need_units,
        })

    # Credit the account. **Idempotency key = tx_hash** — one on-chain transaction is only ever credited once;
    # replaying the same tx_hash can't get a second balance credit.
    res = balance_service.credit(
        db, user_id=claims.account_id, kind="topup_agent", amount_cents=cents,
        idem_key=f"onchain:{tx_hash.lower()}", ref_type="onchain_tx", ref_id=tx_hash,
        note={"network": "base", "asset": "USDC", "units": paid},
    )
    db.commit()
    return {
        "status": "ok" if res.status != "duplicate" else "already_credited",
        "credited_cents": 0 if res.status == "duplicate" else cents,
        "balance_cents": res.balance_cents,
        "tx_hash": tx_hash,
    }
