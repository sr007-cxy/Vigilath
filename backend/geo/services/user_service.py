from typing import Optional, List
from geo.models.user import (
    AGENCY_ROLES,
    DIRECT_ASSIGNABLE_ROLES,
    User,
    UserCreate,
    UserORM,
)
from geo.database import SessionLocal
import bcrypt
import json
from datetime import datetime


def _to_pydantic(db_user: UserORM) -> User:
    # Manual construction historically missed is_admin (the permission field in responses was always False); always go through here
    return User(
        id=db_user.id,
        email=db_user.email,
        name=db_user.name,
        is_active=db_user.is_active,
        is_admin=db_user.is_admin,
        role=getattr(db_user, "role", None) or ("admin" if db_user.is_admin else "user"),
        phone=getattr(db_user, "phone", None),
        wechat_nickname=getattr(db_user, "wechat_nickname", None),
    )


class UserService:
    def __init__(self):
        pass

    def create_user(self, user: UserCreate, role: str = "user") -> User:
        """Create a new user"""
        if role not in DIRECT_ASSIGNABLE_ROLES:
            raise ValueError(f"role must be assigned by its domain service: {role}")
        db = SessionLocal()
        try:
            # Limit password length to 72 bytes for bcrypt
            password = user.password[:72]
            # Hash the password
            password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            
            # Name is optional at the API entry, and the users.name column is nullable.
            # Don't fall back to email -- then customers "without a name" in the admin profile list
            # would show as emails, looking like duplicate data. Keep NULL as NULL;
            # the frontend `a.name || '—'` renders a placeholder.
            db_user = UserORM(
                email=user.email,
                name=(user.name.strip() if user.name and user.name.strip() else None),
                password_hash=password_hash,
                role=role,
                is_admin=(role == "admin"),
            )

            # Add to database
            db.add(db_user)
            db.commit()
            db.refresh(db_user)

            # Convert to Pydantic model
            return _to_pydantic(db_user)
        finally:
            db.close()
    
    def get_user_by_id(self, user_id: int) -> Optional[User]:
        """Get user by ID"""
        db = SessionLocal()
        try:
            db_user = db.query(UserORM).filter(UserORM.id == user_id).first()
            if db_user:
                return _to_pydantic(db_user)
            return None
        finally:
            db.close()

    def get_user_by_email(self, email: str) -> Optional[UserORM]:
        """Get user by email"""
        db = SessionLocal()
        try:
            return db.query(UserORM).filter(UserORM.email == email).first()
        finally:
            db.close()
    
    # ── WeChat QR login ────────────────────────────────────────────────
    # Accounts registered via WeChat have no real email, but users.email is NOT NULL + UNIQUE, so we must fabricate
    # a placeholder address. **Can't use .invalid / .local** -- those are RFC-reserved domains,
    # pydantic's EmailStr rejects them outright, and endpoints like /me with response_model=User
    # 500 entirely (we hit this). Use our own subdomain: valid, and with no MX it can't receive mail.
    WECHAT_EMAIL_DOMAIN = "wx.vigilath.com"

    def get_user_by_phone(self, phone: str) -> Optional[UserORM]:
        db = SessionLocal()
        try:
            return db.query(UserORM).filter(UserORM.phone == phone).first()
        finally:
            db.close()

    def get_user_by_wechat(self, openid: str, unionid: Optional[str] = None) -> Optional[UserORM]:
        """Identify by unionid first -- the same person has different openids across mini program / open platform;
        only unionid is stable across apps. Fall back to openid when there's no unionid."""
        db = SessionLocal()
        try:
            if unionid:
                hit = db.query(UserORM).filter(UserORM.wechat_unionid == unionid).first()
                if hit:
                    return hit
            return db.query(UserORM).filter(UserORM.wechat_openid == openid).first()
        finally:
            db.close()

    def create_phone_user(self, phone: str) -> UserORM:
        """Create an account from a phone number only (SMS code registration).

        Shares the same placeholder email rule as create_wechat_user, so if a phone number first registers via SMS
        and later binds WeChat by QR scan, attach_wechat finds the same row instead of creating two accounts.
        """
        import secrets
        db = SessionLocal()
        try:
            password_hash = bcrypt.hashpw(
                secrets.token_urlsafe(32).encode("utf-8"), bcrypt.gensalt()
            ).decode("utf-8")
            db_user = UserORM(
                email=f"{phone}@{self.WECHAT_EMAIL_DOMAIN}",
                name=None,
                password_hash=password_hash,
                role="user",
                is_admin=False,
                phone=phone,
                phone_verified_at=datetime.utcnow(),
                wechat_subscribed=False,
            )
            db.add(db_user)
            db.commit()
            db.refresh(db_user)
            db.expunge(db_user)
            return db_user
        finally:
            db.close()

    def create_wechat_user(self, phone: str, openid: str, unionid: Optional[str] = None,
                           nickname: Optional[str] = None, avatar: Optional[str] = None) -> UserORM:
        """Create an account from WeChat + phone. The password is a random string never disclosed -- such accounts can only log in by QR scan."""
        import secrets
        db = SessionLocal()
        try:
            password_hash = bcrypt.hashpw(
                secrets.token_urlsafe(32).encode("utf-8"), bcrypt.gensalt()
            ).decode("utf-8")
            db_user = UserORM(
                email=f"{phone}@{self.WECHAT_EMAIL_DOMAIN}",
                name=nickname or None,
                password_hash=password_hash,
                role="user",
                is_admin=False,
                phone=phone,
                phone_verified_at=datetime.utcnow(),
                wechat_openid=openid,
                wechat_unionid=unionid,
                wechat_nickname=nickname,
                wechat_avatar=avatar,
                wechat_subscribed=True,
            )
            db.add(db_user)
            db.commit()
            db.refresh(db_user)
            db.expunge(db_user)
            return db_user
        finally:
            db.close()

    def attach_wechat(self, user_id: int, openid: str, unionid: Optional[str] = None,
                      nickname: Optional[str] = None, avatar: Optional[str] = None,
                      phone: Optional[str] = None) -> Optional[UserORM]:
        """Attach a WeChat identity / phone number to an existing account (old email users' first QR scan goes here)."""
        db = SessionLocal()
        try:
            db_user = db.query(UserORM).filter(UserORM.id == user_id).first()
            if not db_user:
                return None
            db_user.wechat_openid = openid
            if unionid:
                db_user.wechat_unionid = unionid
            if nickname:
                db_user.wechat_nickname = nickname
            if avatar:
                db_user.wechat_avatar = avatar
            db_user.wechat_subscribed = True
            if phone and not db_user.phone:
                db_user.phone = phone
                db_user.phone_verified_at = datetime.utcnow()
            db.commit()
            db.refresh(db_user)
            db.expunge(db_user)
            return db_user
        finally:
            db.close()

    def set_wechat_subscribed(self, openid: str, subscribed: bool) -> None:
        """Unfollowing only changes a flag and doesn't disable the account -- unfollowing shouldn't lock the person out."""
        db = SessionLocal()
        try:
            db_user = db.query(UserORM).filter(UserORM.wechat_openid == openid).first()
            if db_user:
                db_user.wechat_subscribed = subscribed
                db.commit()
        finally:
            db.close()

    def get_user_by_username(self, username: str) -> Optional[UserORM]:
        """Get user by username (email)"""
        return self.get_user_by_email(username)
    
    def get_all_users(self) -> List[User]:
        """Get all users"""
        db = SessionLocal()
        try:
            db_users = db.query(UserORM).all()
            return [_to_pydantic(user) for user in db_users]
        finally:
            db.close()

    def update_user_role(self, user_id: int, role: str) -> Optional[User]:
        """Change role -- the single write point keeping role and is_admin in sync"""
        if role not in DIRECT_ASSIGNABLE_ROLES:
            raise ValueError(f"role must be assigned by its domain service: {role}")
        db = SessionLocal()
        try:
            db_user = db.query(UserORM).filter(UserORM.id == user_id).first()
            if not db_user:
                return None
            if db_user.role in AGENCY_ROLES:
                raise ValueError("agency role must be removed by organization service")
            db_user.role = role
            db_user.is_admin = (role == "admin")
            db.commit()
            db.refresh(db_user)
            return _to_pydantic(db_user)
        finally:
            db.close()
    
    def update_user(self, user_id: int, user_update: UserCreate) -> Optional[User]:
        """Update user"""
        db = SessionLocal()
        try:
            db_user = db.query(UserORM).filter(UserORM.id == user_id).first()
            if db_user:
                # Limit password length to 72 bytes for bcrypt
                password = user_update.password[:72]
                # Hash the password
                password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                
                db_user.email = user_update.email
                if user_update.name is not None:
                    db_user.name = user_update.name
                db_user.password_hash = password_hash
                
                db.commit()
                db.refresh(db_user)

                return _to_pydantic(db_user)
            return None
        finally:
            db.close()
    
    def delete_user(self, user_id: int) -> bool:
        """Delete user"""
        db = SessionLocal()
        try:
            db_user = db.query(UserORM).filter(UserORM.id == user_id).first()
            if db_user:
                if db_user.role in AGENCY_ROLES:
                    raise ValueError("agency member must be removed by organization service")
                db.delete(db_user)
                db.commit()
                return True
            return False
        finally:
            db.close()
    
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify password"""
        try:
            # Limit password length to 72 bytes for bcrypt
            password = plain_password[:72]
            return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))
        except Exception as e:
            return False

# Create and export user_service instance
user_service = UserService()
