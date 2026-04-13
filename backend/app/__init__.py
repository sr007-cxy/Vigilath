from app.database import Base, engine
from app.models.user import UserORM
from app.models.membership import MembershipORM, UserMembershipORM

# Create all tables
Base.metadata.create_all(bind=engine)