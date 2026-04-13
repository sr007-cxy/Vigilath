from typing import Optional, List
from datetime import datetime, timedelta
from app.models.membership import Membership, MembershipCreate, UserMembership, UserMembershipCreate, MembershipORM, UserMembershipORM
from app.database import SessionLocal
import json

class MembershipService:
    def __init__(self):
        # Initialize default membership tiers
        self._initialize_default_memberships()
    
    def _initialize_default_memberships(self):
        """Initialize default membership tiers"""
        db = SessionLocal()
        try:
            # Check if memberships already exist
            existing_memberships = db.query(MembershipORM).count()
            if existing_memberships > 0:
                return
            
            default_memberships = [
                {
                    "name": "免费版",
                    "price": 0.0,
                    "period": "/永久",
                    "description": "适合个人用户和小型网站的基础检测服务",
                    "popular": False,
                    "features": [
                        "基础GEO配置检测",
                        "简单的优化建议",
                        "检测结果报告",
                        "有限的结果查看",
                        "每月3次检测限制"
                    ],
                    "not_included": [
                        "详细的优化建议",
                        "优先级排序",
                        "完整的结果报告",
                        "技术支持"
                    ]
                },
                {
                    "name": "基础会员",
                    "price": 99.0,
                    "period": "/月",
                    "description": "适合需要完整检测结果的网站管理员",
                    "popular": True,
                    "features": [
                        "全面的GEO配置检测",
                        "详细的优化建议",
                        "优先级排序",
                        "完整的结果报告",
                        "每月10次检测",
                        "基础技术支持"
                    ],
                    "not_included": [
                        "专业优化方案",
                        "批量检测",
                        "优先技术支持",
                        "专属顾问"
                    ]
                },
                {
                    "name": "高级会员",
                    "price": 299.0,
                    "period": "/月",
                    "description": "适合专业SEO优化人员和数字营销团队",
                    "popular": False,
                    "features": [
                        "所有检测服务",
                        "专业优化方案",
                        "优先级排序",
                        "完整的结果报告",
                        "每月30次检测",
                        "优先技术支持",
                        "定期GEO趋势报告"
                    ],
                    "not_included": [
                        "批量检测",
                        "专属顾问",
                        "定制化服务"
                    ]
                },
                {
                    "name": "企业会员",
                    "price": 999.0,
                    "period": "/月",
                    "description": "适合大型企业和机构的全面GEO解决方案",
                    "popular": False,
                    "features": [
                        "所有检测服务",
                        "专业优化方案",
                        "批量检测",
                        "专属顾问",
                        "无限次检测",
                        "24/7优先技术支持",
                        "定制化GEO策略",
                        "定期GEO趋势报告"
                    ],
                    "not_included": []
                }
            ]
            
            for membership_data in default_memberships:
                # Create ORM instance
                db_membership = MembershipORM(
                    name=membership_data["name"],
                    price=membership_data["price"],
                    period=membership_data["period"],
                    description=membership_data["description"],
                    popular=membership_data["popular"],
                    features=json.dumps(membership_data["features"]),
                    not_included=json.dumps(membership_data["not_included"])
                )
                
                # Add to database
                db.add(db_membership)
            
            db.commit()
        finally:
            db.close()
    
    def create_membership(self, membership: MembershipCreate) -> Membership:
        """Create a new membership tier"""
        db = SessionLocal()
        try:
            # Create ORM instance
            db_membership = MembershipORM(
                name=membership.name,
                price=membership.price,
                period=membership.period,
                description=membership.description,
                popular=membership.popular,
                features=json.dumps(membership.features),
                not_included=json.dumps(membership.not_included)
            )
            
            # Add to database
            db.add(db_membership)
            db.commit()
            db.refresh(db_membership)
            
            # Convert to Pydantic model
            return Membership(
                id=db_membership.id,
                name=db_membership.name,
                price=db_membership.price,
                period=db_membership.period,
                description=db_membership.description,
                popular=db_membership.popular,
                features=json.loads(db_membership.features),
                not_included=json.loads(db_membership.not_included),
                created_at=db_membership.created_at,
                updated_at=db_membership.updated_at
            )
        finally:
            db.close()
    
    def get_membership_by_id(self, membership_id: int) -> Optional[Membership]:
        """Get membership by ID"""
        db = SessionLocal()
        try:
            db_membership = db.query(MembershipORM).filter(MembershipORM.id == membership_id).first()
            if db_membership:
                return Membership(
                    id=db_membership.id,
                    name=db_membership.name,
                    price=db_membership.price,
                    period=db_membership.period,
                    description=db_membership.description,
                    popular=db_membership.popular,
                    features=json.loads(db_membership.features),
                    not_included=json.loads(db_membership.not_included),
                    created_at=db_membership.created_at,
                    updated_at=db_membership.updated_at
                )
            return None
        finally:
            db.close()
    
    def get_all_memberships(self) -> List[Membership]:
        """Get all membership tiers"""
        db = SessionLocal()
        try:
            db_memberships = db.query(MembershipORM).all()
            return [
                Membership(
                    id=membership.id,
                    name=membership.name,
                    price=membership.price,
                    period=membership.period,
                    description=membership.description,
                    popular=membership.popular,
                    features=json.loads(membership.features),
                    not_included=json.loads(membership.not_included),
                    created_at=membership.created_at,
                    updated_at=membership.updated_at
                )
                for membership in db_memberships
            ]
        finally:
            db.close()
    
    def update_membership(self, membership_id: int, membership_update: MembershipCreate) -> Optional[Membership]:
        """Update membership tier"""
        db = SessionLocal()
        try:
            db_membership = db.query(MembershipORM).filter(MembershipORM.id == membership_id).first()
            if db_membership:
                db_membership.name = membership_update.name
                db_membership.price = membership_update.price
                db_membership.period = membership_update.period
                db_membership.description = membership_update.description
                db_membership.popular = membership_update.popular
                db_membership.features = json.dumps(membership_update.features)
                db_membership.not_included = json.dumps(membership_update.not_included)
                
                db.commit()
                db.refresh(db_membership)
                
                return Membership(
                    id=db_membership.id,
                    name=db_membership.name,
                    price=db_membership.price,
                    period=db_membership.period,
                    description=db_membership.description,
                    popular=db_membership.popular,
                    features=json.loads(db_membership.features),
                    not_included=json.loads(db_membership.not_included),
                    created_at=db_membership.created_at,
                    updated_at=db_membership.updated_at
                )
            return None
        finally:
            db.close()
    
    def delete_membership(self, membership_id: int) -> bool:
        """Delete membership tier"""
        db = SessionLocal()
        try:
            db_membership = db.query(MembershipORM).filter(MembershipORM.id == membership_id).first()
            if db_membership:
                db.delete(db_membership)
                db.commit()
                return True
            return False
        finally:
            db.close()
    
    def create_user_membership(self, user_membership: UserMembershipCreate) -> UserMembership:
        """Create a new user membership"""
        db = SessionLocal()
        try:
            # Create ORM instance
            db_user_membership = UserMembershipORM(
                user_id=user_membership.user_id,
                membership_id=user_membership.membership_id,
                start_date=user_membership.start_date,
                end_date=user_membership.end_date,
                is_active=True
            )
            
            # Add to database
            db.add(db_user_membership)
            db.commit()
            db.refresh(db_user_membership)
            
            # Convert to Pydantic model
            return UserMembership(
                id=db_user_membership.id,
                user_id=db_user_membership.user_id,
                membership_id=db_user_membership.membership_id,
                start_date=db_user_membership.start_date,
                end_date=db_user_membership.end_date,
                is_active=db_user_membership.is_active
            )
        finally:
            db.close()
    
    def get_user_membership(self, user_id: int) -> Optional[UserMembership]:
        """Get user's current membership"""
        db = SessionLocal()
        try:
            # Get active membership
            db_user_membership = db.query(UserMembershipORM).filter(
                UserMembershipORM.user_id == user_id,
                UserMembershipORM.is_active == True
            ).first()
            
            if db_user_membership:
                # Check if membership is still valid
                if datetime.now() < db_user_membership.end_date:
                    return UserMembership(
                        id=db_user_membership.id,
                        user_id=db_user_membership.user_id,
                        membership_id=db_user_membership.membership_id,
                        start_date=db_user_membership.start_date,
                        end_date=db_user_membership.end_date,
                        is_active=db_user_membership.is_active
                    )
                else:
                    # Deactivate expired membership
                    db_user_membership.is_active = False
                    db.commit()
            return None
        finally:
            db.close()
    
    def upgrade_membership(self, user_id: int, new_membership_id: int) -> Optional[UserMembership]:
        """Upgrade user's membership"""
        db = SessionLocal()
        try:
            # Deactivate current membership
            current_membership = db.query(UserMembershipORM).filter(
                UserMembershipORM.user_id == user_id,
                UserMembershipORM.is_active == True
            ).first()
            
            if current_membership:
                current_membership.is_active = False
                db.commit()
            
            # Create new membership
            new_membership = db.query(MembershipORM).filter(MembershipORM.id == new_membership_id).first()
            if not new_membership:
                return None
            
            # Calculate end date based on membership period
            start_date = datetime.now()
            if "/月" in new_membership.period:
                end_date = start_date + timedelta(days=30)
            elif "/年" in new_membership.period:
                end_date = start_date + timedelta(days=365)
            else:
                # Permanent membership
                end_date = start_date + timedelta(days=36500)  # ~100 years
            
            # Create ORM instance
            db_user_membership = UserMembershipORM(
                user_id=user_id,
                membership_id=new_membership_id,
                start_date=start_date,
                end_date=end_date,
                is_active=True
            )
            
            # Add to database
            db.add(db_user_membership)
            db.commit()
            db.refresh(db_user_membership)
            
            # Convert to Pydantic model
            return UserMembership(
                id=db_user_membership.id,
                user_id=db_user_membership.user_id,
                membership_id=db_user_membership.membership_id,
                start_date=db_user_membership.start_date,
                end_date=db_user_membership.end_date,
                is_active=db_user_membership.is_active
            )
        finally:
            db.close()
    
    def cancel_membership(self, user_id: int) -> bool:
        """Cancel user's membership"""
        db = SessionLocal()
        try:
            user_membership = db.query(UserMembershipORM).filter(
                UserMembershipORM.user_id == user_id,
                UserMembershipORM.is_active == True
            ).first()
            if user_membership:
                user_membership.is_active = False
                db.commit()
                return True
            return False
        finally:
            db.close()

membership_service = MembershipService()