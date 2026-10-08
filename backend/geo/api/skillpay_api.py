"""/api/skillpay/* — Pay Skill paid entry (WeChat Agent Pay / X402).

**Billing boundary: audits are always free.** There are only two paid points here:
  - `subscribe` activates a continuous monitoring monthly subscription (watch10 / watch30 / watch100)
  - `topup` tops up the compute-points wallet (for existing customers used to the wallet)

Implemented per SkillHub's official mch-demo **402 → query order → fulfill** pattern: one endpoint, two scenarios,
routed by the `X-Out-Trade-No` request header:

    without the header → 402 + WeixinPay-Required + X-Out-Trade-No + amount and description
    with the header → query the WeChat order → fulfill only on SUCCESS → 200

Four hard rules (per the demo, each with tests):

1. **Business failures must be refunded**. If money is collected but the subscription cannot be activated, refund via the original route; if the refund also fails, give a support path.
2. **Idempotent fulfillment**. Already FULFILLED orders return result_json directly, never activating twice.
3. **Order-query failures return 402, not 5xx**. Network jitter ≠ failure; on a 5xx the assistant would give up as a failure,
   while the user has already paid.
4. **Order ownership check**. Retrying with someone else's order number always gets 404.

Do not mix the two sets of keys: the WeChat API certificate is for ordering/querying/refunds; the SkillHub developer key is only for AI pre-ordering.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from geo.database import SessionLocal
from geo.models.membership import MembershipORM
from geo.models.skillpay import SkillPayOrderORM
from geo.models.user import UserORM

log = logging.getLogger(__name__)
router = APIRouter(prefix="/skillpay")

SKILL_ID = "vigilath-geo"
SKILL_VERSION = "1.0.0"
WATCH_PLANS = ("watch10", "watch30", "watch100")     # Three continuous monitoring tiers


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _account(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> UserORM:
    from agents.geo.embed.tokens import EmbedAuthError, verify_token

    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    try:
        claims = verify_token(db, token)
    except EmbedAuthError as e:
        raise HTTPException(status_code=e.code, detail=e.detail)
    user = db.get(UserORM, claims.account_id)
    if user is None:
        raise HTTPException(status_code=401, detail="账号不存在")
    return user


def _cny(cents: int) -> str:
    return f"{cents / 100:.2f}"


def _402(payment_code: str, out_trade_no: str, amount_cents: int, description: str) -> JSONResponse:
    """402 response — the payment code and order number in both headers and body (some assistants only read the body)."""
    return JSONResponse(
        status_code=402,
        headers={"WeixinPay-Required": payment_code, "X-Out-Trade-No": out_trade_no},
        content={
            "code": "PAYMENT_REQUIRED",
            "message": "需要支付后才能开通",
            "WeixinPay": {
                "WeixinPay-Required": payment_code,
                "prompt": ("本次使用微信支付,请将 WeixinPay-Required 的值作为 paymentCode "
                           "交给 weixinpay_pay,以向用户申请支付授权。"),
            },
            "out_trade_no": out_trade_no,
            "amount": _cny(amount_cents),
            "currency": "CNY",
            "description": description,
            "next_step": ("用户授权成功后,**带上 X-Out-Trade-No 请求头**再请求本接口一次(body 不变),"
                          "才会真正开通。漏了这一步就永远拿不到结果。"),
        },
    )


def _order_of(db: Session, out_trade_no: str, account_id: int) -> SkillPayOrderORM | None:
    row = (db.query(SkillPayOrderORM)
             .filter(SkillPayOrderORM.out_trade_no == out_trade_no)
             .first())
    return row if (row and row.account_id == account_id) else None


def _fulfill_subscribe(db: Session, order: SkillPayOrderORM) -> dict:
    """Actually activate the subscription. Raising = business failure; the caller triggers a refund."""
    from geo.services.membership_service import membership_service

    params = json.loads(order.params_json or "{}")
    slug = params.get("plan_slug")
    plan = db.query(MembershipORM).filter(MembershipORM.slug == slug).first()
    if plan is None:
        raise RuntimeError(f"套餐 {slug} 不存在")
    membership_service.upgrade_membership(order.account_id, plan.id)
    return {"plan_slug": slug, "plan_name": plan.name, "period": plan.period}


def _refund_and_reply(db: Session, order: SkillPayOrderORM, err: str) -> JSONResponse:
    """Business failure → refund via the original route. If the refund also fails, give a support path rather than pretending all is well."""
    from geo.services.wechat_pay_service import wechat_pay_service

    res = wechat_pay_service.refund(order.out_trade_no, order.amount_cents, reason=err[:80])
    order.error = err[:512]
    if res.get("ok"):
        order.status = "REFUNDED"
        order.refund_id = res.get("refund_id") or ""
        order.refunded_at = datetime.utcnow()
        db.commit()
        return JSONResponse(status_code=200, content={
            "code": "REFUNDED",
            "message": "服务异常无法完成开通,已自动退款至原支付账户。",
            "out_trade_no": order.out_trade_no,
            "amount": _cny(order.amount_cents),
            "note": "钱已按原路退回(到账以微信为准)。**不要再次支付。**",
        })
    order.status = "REFUND_FAILED"
    db.commit()
    log.error("[skillpay] refund failed out_trade_no=%s: %s", order.out_trade_no, res.get("error"))
    return JSONResponse(status_code=500, content={
        "code": "FULFILL_AND_REFUND_FAILED",
        "message": "服务异常且自动退款未成功,请联系客服处理,不要重复支付。",
        "out_trade_no": order.out_trade_no,
    })


class SubscribeIn(BaseModel):
    plan_slug: str = "watch30"


@router.post("/subscribe")
async def skillpay_subscribe(
    body: SubscribeIn,
    user: UserORM = Depends(_account),
    db: Session = Depends(get_db),
    x_out_trade_no: str = Header(default="", alias="X-Out-Trade-No"),
):
    """Activate a continuous monitoring monthly subscription. **With the X-Out-Trade-No header = retry after payment**, using query-and-fulfill."""
    from geo.services import skillpay
    from geo.services.wechat_pay_service import wechat_pay_service

    # ── Scenario 2: retry after payment
    if x_out_trade_no.strip():
        out_trade_no = x_out_trade_no.strip()
        order = _order_of(db, out_trade_no, user.id)
        if order is None:
            raise HTTPException(status_code=404, detail="订单不存在或不属于当前账号")

        # Idempotent: if already fulfilled return the cache, never activate twice
        if order.status == "FULFILLED":
            return {"code": "SUCCESS", "message": "已开通(本次为重复请求)",
                    "out_trade_no": out_trade_no, "already_fulfilled": True,
                    "result": json.loads(order.result_json or "{}")}
        if order.status in ("REFUNDED", "REFUND_FAILED"):
            return {"code": order.status, "message": "本单此前已异常并处理过,请勿重复支付",
                    "out_trade_no": out_trade_no}

        try:
            info = wechat_pay_service.query_order(out_trade_no)
        except Exception as e:                       # noqa: BLE001
            log.warning("[skillpay] order query failed %s: %s", out_trade_no, e)
            # ★ Return 402 rather than 5xx: most likely network jitter; the assistant can retry later with the same order number
            return JSONResponse(status_code=402, content={
                "code": "NOT_PAID", "message": "支付状态校验中,请稍后带同一个 X-Out-Trade-No 重试",
                "out_trade_no": out_trade_no})
        state = str((info or {}).get("trade_state") or "").upper()
        if state != "SUCCESS":
            return JSONResponse(status_code=402, content={
                "code": "NOT_PAID", "message": "订单尚未支付完成",
                "out_trade_no": out_trade_no, "trade_state": state or "UNKNOWN",
                "note": "用户还没付完。等他授权后带同一个订单号再来,**不要重新下单**。"})

        order.transaction_id = str((info or {}).get("transaction_id") or "")
        try:
            result = _fulfill_subscribe(db, order)
        except Exception as e:                       # noqa: BLE001
            log.warning("[skillpay] activation failed, preparing refund %s: %s", out_trade_no, e)
            return _refund_and_reply(db, order, str(e))

        order.status = "FULFILLED"
        order.result_json = json.dumps(result, ensure_ascii=False)
        order.fulfilled_at = datetime.utcnow()
        db.commit()
        return {"code": "SUCCESS", "message": f"已开通「{result['plan_name']}」",
                "out_trade_no": out_trade_no, "result": result}

    # ── Scenario 1: first request
    slug = (body.plan_slug or "").strip()
    if slug not in WATCH_PLANS:
        raise HTTPException(status_code=400, detail=f"套餐只能是:{' / '.join(WATCH_PLANS)}")
    plan = db.query(MembershipORM).filter(MembershipORM.slug == slug).first()
    if plan is None:
        raise HTTPException(status_code=404, detail=f"套餐 {slug} 未上架")
    if not skillpay.configured():
        return JSONResponse(status_code=503, content={
            "code": "PAY_UNAVAILABLE",
            "message": "本站未开通微信 Agent Pay,请到网页购买",
            "web": "/products-services"})

    order_info = wechat_pay_service.create_native_order(user=user, membership_slug=slug)
    out_trade_no = order_info["out_trade_no"]
    amount_cents = int(round(float(order_info.get("amount_cny") or 0) * 100))

    db.add(SkillPayOrderORM(
        out_trade_no=out_trade_no, account_id=user.id, feature=f"subscribe:{slug}",
        params_json=json.dumps({"plan_slug": slug}, ensure_ascii=False),
        amount_cents=amount_cents, status="CREATED", created_at=datetime.utcnow(),
    ))
    db.commit()

    biz = skillpay.build_business_json(
        skill_id=SKILL_ID, skill_version=SKILL_VERSION, product_id=out_trade_no,
        pay_type_value=order_info["code_url"], pay_data_type="code_url")
    try:
        payment_code = await skillpay.preorder(biz)
    except skillpay.SkillPayNotConfigured as e:
        return JSONResponse(status_code=503, content={"code": "PAY_UNAVAILABLE", "message": str(e)})
    except skillpay.SkillPayError as e:
        log.warning("[skillpay] pre-order failed %s: %s", out_trade_no, e)
        return JSONResponse(status_code=502, content={
            "code": "PREORDER_FAILED",
            "message": "微信支付预下单没成功,请稍后再试或到网页购买",
            "web": "/products-services"})

    return _402(payment_code, out_trade_no, amount_cents, f"{plan.name}(月订阅)")


@router.get("/plans")
async def skillpay_plans(db: Session = Depends(get_db)):
    """Purchasable monthly subscription tiers. **Queryable without login** — assistants need prices before recommending."""
    from geo.database import settings

    rate = float(getattr(settings, "USD_TO_CNY_RATE", 7.2) or 7.2)
    rows = (db.query(MembershipORM)
              .filter(MembershipORM.slug.in_(WATCH_PLANS))
              .order_by(MembershipORM.display_order).all())
    return {
        "plans": [{
            "slug": m.slug, "name": m.name,
            "price_cny": round(float(m.price) * rate, 2),
            "period": m.period, "description": m.description,
            "features": json.loads(m.features or "[]"),
            "popular": bool(m.popular),
        } for m in rows],
        "note": "检测能力一律免费,不需要订阅。订阅买的是**持续监测**:定时跑批与日周月报。",
    }


@router.get("/status")
async def skillpay_status():
    """Ops self-check: is everything configured. **Never echoes any key content**."""
    from geo.services import skillpay

    return {
        "configured": skillpay.configured(),
        "developer_id_set": bool(skillpay.developer_id()),
        "pub_key_id_set": bool(skillpay.pub_key_id()),
        "private_key_loaded": bool(skillpay._private_key_pem()),
        "skill_id": SKILL_ID, "skill_version": SKILL_VERSION,
        "plans": list(WATCH_PLANS),
    }
