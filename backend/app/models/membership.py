from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from app.database import Base

# SQLAlchemy ORM Models
class MembershipORM(Base):
    __tablename__ = "memberships"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    price = Column(Float, nullable=False)
    period = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    popular = Column(Boolean, default=False)
    features = Column(String, nullable=False)  # Store as JSON string
    not_included = Column(String, nullable=False)  # Store as JSON string
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class UserMembershipORM(Base):
    __tablename__ = "user_memberships"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    membership_id = Column(Integer, ForeignKey("memberships.id"), nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    is_active = Column(Boolean, default=True)

# Pydantic Models
class MembershipBase(BaseModel):
    name: str
    price: float
    period: str
    description: str
    popular: bool = False

class MembershipCreate(MembershipBase):
    features: List[str]
    not_included: List[str]

class Membership(MembershipBase):
    id: int
    features: List[str]
    not_included: List[str]
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

class UserMembership(BaseModel):
    id: int
    user_id: int
    membership_id: int
    start_date: datetime
    end_date: datetime
    is_active: bool = True
    
    class Config:
        from_attributes = True

class UserMembershipCreate(BaseModel):
    user_id: int
    membership_id: int
    start_date: datetime
    end_date: datetime

class MembershipUpgrade(BaseModel):
    user_id: int
    new_membership_id: int