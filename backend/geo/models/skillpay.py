"""SkillPay order fulfillment records -- the basis for idempotency and refunds.

`payment_sessions` only records "was the money received", not "was the item delivered". Pay-per-use/per-order must answer
three questions, all relying on this table:

1. **Has this order been fulfilled?** -- assistant retries are common; already fulfilled orders return result_json directly,
   never re-running the paid business logic (re-activating subscriptions or double-deducting quota are incidents)
2. **The business failed -- was it refunded?** -- if money was taken and the job failed, refund it; refund status must be traceable
3. **Whose order is this?** -- ownership is checked on retry, preventing freeloading with someone else's order number
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, Text

from geo.database import Base


class SkillPayOrderORM(Base):
    __tablename__ = "skillpay_orders"
    __table_args__ = (Index("ix_skillpay_orders_account", "account_id", "created_at"),)

    id = Column(Integer, primary_key=True, index=True)
    out_trade_no = Column(String(length=64), nullable=False, unique=True, index=True)  # WeChat order number, <= 32 chars
    account_id = Column(Integer, nullable=False, index=True)      # Owning account, checked on retry
    feature = Column(String(length=64), nullable=False)           # subscribe:watch30 / topup / ...
    params_json = Column(Text, nullable=False, default="{}")      # First request params; on retry the body is unchanged, used to re-run
    amount_cents = Column(Integer, nullable=False, default=0)
    # CREATED → FULFILLED | REFUNDED | REFUND_FAILED
    status = Column(String(length=24), nullable=False, default="CREATED", index=True)
    result_json = Column(Text, nullable=True)                     # Fulfillment result -- key to idempotency
    transaction_id = Column(String(length=64), nullable=False, default="")   # WeChat transaction id
    refund_id = Column(String(length=64), nullable=False, default="")
    error = Column(String(length=512), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    fulfilled_at = Column(DateTime, nullable=True)
    refunded_at = Column(DateTime, nullable=True)
