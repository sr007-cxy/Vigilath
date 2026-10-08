"""Per-user ownership records for accounts authorized through AiToEarn."""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text

from geo.database import Base


class AitoearnAccountBindingORM(Base):
    __tablename__ = "aitoearn_account_bindings"
    __table_args__ = (
        Index("ix_aitoearn_binding_user", "user_id"),
        Index("uq_aitoearn_binding_upstream", "upstream_account_id", unique=True),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    upstream_account_id = Column(String(128), nullable=False)
    account_type = Column(String(32), nullable=False)
    display_name = Column(String(255), nullable=False, default="")
    avatar_url = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class AitoearnAuthAttemptORM(Base):
    __tablename__ = "aitoearn_auth_attempts"
    __table_args__ = (Index("ix_aitoearn_auth_attempt_user", "user_id"),)

    id = Column(String(36), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    platform = Column(String(32), nullable=False)
    upstream_session_id = Column(String(128), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
