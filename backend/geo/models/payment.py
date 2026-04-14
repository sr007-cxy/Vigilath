from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text
from geo.database import Base


class PaymentSessionORM(Base):
    """Tracks a Stripe Checkout Session from creation through fulfillment.

    One row per checkout attempt. We store `status` separately from Stripe's
    session state so the frontend can poll us (not Stripe) to confirm a
    subscription was activated, and the webhook handler remains the single
    source of truth for fulfillment.
    """

    __tablename__ = "payment_sessions"

    id = Column(Integer, primary_key=True, index=True)
    stripe_session_id = Column(String, unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    membership_id = Column(Integer, ForeignKey("memberships.id"), nullable=False)
    amount_cents = Column(Integer, nullable=False)
    currency = Column(String, nullable=False, default="usd")
    # pending | paid | expired | failed
    status = Column(String, nullable=False, default="pending", index=True)
    stripe_payment_intent_id = Column(String, nullable=True)
    checkout_url = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
